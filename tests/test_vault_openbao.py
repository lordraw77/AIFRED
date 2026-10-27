"""Tests for the OpenBao secrets backend, the migration and the outage behaviour."""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from aifred.app import Services, create_app
from aifred.config import SettingError, Settings
from aifred.llm import LLMError, Outcome
from aifred.providers import ProviderError
from aifred.store import Store
from aifred.vault import OpenBaoBox, SecretBox, Vault, VaultError, VaultUnavailable, load_or_create_key
from tests.conftest import ADMIN_PASSWORD, ADMIN_USER, login
from tests.fake_llm import FakeLLM, Text
from tests.fake_openbao import FakeOpenBao

pytestmark = pytest.mark.anyio


def approle(bao: FakeOpenBao, **kw: Any) -> OpenBaoBox:
    return OpenBaoBox(bao.url, role_id=bao.ROLE_ID, secret_id=bao.SECRET_ID, **kw)


def settings_for(tmp_path: Path, bao: FakeOpenBao | None = None, backend: str = "openbao", **kw: Any) -> Settings:
    base: dict[str, Any] = dict(
        _env_file=None, admin_user=ADMIN_USER, admin_password=SecretStr(ADMIN_PASSWORD), data_dir=tmp_path / "data",
        secrets_backend=backend,
    )
    if bao is not None:
        base |= dict(openbao_addr=bao.url, openbao_role_id=bao.ROLE_ID, openbao_secret_id=SecretStr(bao.SECRET_ID))
    return Settings(**(base | kw))


# The box


def test_round_trip_with_approle(fake_openbao: FakeOpenBao) -> None:
    box = approle(fake_openbao)
    blob = box.encrypt("gsk_super_secret", "provider_key:1")
    assert blob.startswith(b"vault:v1:") and b"super_secret" not in blob
    assert box.decrypt(blob, "provider_key:1") == "gsk_super_secret"
    assert fake_openbao.logins == 1  # logged in once, lazily
    box.encrypt("x", "n")
    assert fake_openbao.logins == 1


def test_ciphertext_is_bound_to_the_name(fake_openbao: FakeOpenBao) -> None:
    box = approle(fake_openbao)
    blob = box.encrypt("value", "a")
    with pytest.raises(VaultError, match="different name"):
        box.decrypt(blob, "b")


def test_static_token(fake_openbao: FakeOpenBao) -> None:
    box = OpenBaoBox(fake_openbao.url, token="static-token")
    assert box.decrypt(box.encrypt("v", "n"), "n") == "v"
    assert fake_openbao.calls["login"] == 0
    bad = OpenBaoBox(fake_openbao.url, token="revoked")
    with pytest.raises(VaultUnavailable, match="denied access"):
        bad.encrypt("v", "n")


def test_expired_approle_token_logs_in_again(fake_openbao: FakeOpenBao) -> None:
    box = approle(fake_openbao)
    blob = box.encrypt("v", "n")
    fake_openbao.expire_tokens()
    assert box.decrypt(blob, "n") == "v"
    assert fake_openbao.logins == 2


def test_wrong_approle_credentials(fake_openbao: FakeOpenBao) -> None:
    box = OpenBaoBox(fake_openbao.url, role_id="nope", secret_id="nope")
    with pytest.raises(VaultUnavailable, match="rejected the AppRole login"):
        box.encrypt("v", "n")


def test_sealed_is_an_outage_not_an_error(fake_openbao: FakeOpenBao) -> None:
    box = approle(fake_openbao)
    blob = box.encrypt("v", "n")
    fake_openbao.sealed = True
    for action in (lambda: box.encrypt("v", "n"), lambda: box.decrypt(blob, "n")):
        with pytest.raises(VaultUnavailable, match="sealed"):
            action()
    assert box.health() == (False, "sealed")
    fake_openbao.sealed = False
    assert box.decrypt(blob, "n") == "v" and box.health() == (True, "ready")


def test_unreachable_server_is_an_outage(fake_openbao: FakeOpenBao) -> None:
    box = approle(fake_openbao)
    fake_openbao.stop()
    with pytest.raises(VaultUnavailable, match="unreachable"):
        box.encrypt("v", "n")
    ok, detail = box.health()
    assert not ok and "unreachable" in detail


def test_corrupt_ciphertext_is_a_permanent_error(fake_openbao: FakeOpenBao) -> None:
    box = approle(fake_openbao)
    with pytest.raises(VaultError) as exc:
        box.decrypt(b"vault:v1:AAAA", "n")
    assert not isinstance(exc.value, VaultUnavailable)


def test_credentials_are_required() -> None:
    with pytest.raises(VaultError, match="token, or a role id and a secret id"):
        OpenBaoBox("http://x", role_id="only-role")


# The vault on top of it


def test_vault_caches_decrypted_values(store: Store, fake_openbao: FakeOpenBao) -> None:
    vault = Vault(store, approle(fake_openbao))
    vault.set("s", "value-1")
    assert vault.get("s") == "value-1" and vault.get("s") == "value-1"
    assert fake_openbao.calls["decrypt"] == 0  # set() primed the cache
    fresh = Vault(store, approle(fake_openbao))
    assert fresh.get("s") == "value-1" and fresh.get("s") == "value-1"
    assert fake_openbao.calls["decrypt"] == 1
    vault.set("s", "value-2")  # another process changes the row: the cache notices by the blob
    assert fresh.get("s") == "value-2"
    assert fake_openbao.calls["decrypt"] == 2
    assert vault.delete("s") and vault.get("s") is None and not vault.exists("s")


def test_database_holds_only_ciphertext(store: Store, fake_openbao: FakeOpenBao) -> None:
    Vault(store, approle(fake_openbao)).set("provider_key:1", "gsk_NEVER_IN_THE_DB_1234")
    assert b"NEVER_IN_THE_DB" not in Path(store.path).read_bytes()
    assert b"NEVER_IN_THE_DB" not in Path(store.path + "-wal").read_bytes()
    assert store.secret_get("provider_key:1").startswith(b"vault:")  # type: ignore[union-attr]


def test_vault_get_raises_during_an_outage_but_exists_does_not(store: Store, fake_openbao: FakeOpenBao) -> None:
    vault = Vault(store, approle(fake_openbao))
    vault.set("s", "v")
    fresh = Vault(store, approle(fake_openbao))
    fake_openbao.sealed = True
    with pytest.raises(VaultUnavailable):
        fresh.get("s")
    assert fresh.exists("s") and fresh.status()["ok"] is False and fresh.status()["detail"] == "sealed"
    with pytest.raises(VaultUnavailable):
        fresh.set("t", "x")
    assert not fresh.exists("t")  # a failed write leaves nothing behind


def test_migration_from_file_to_openbao(tmp_path: Path, store: Store, fake_openbao: FakeOpenBao) -> None:
    local = SecretBox(load_or_create_key(tmp_path / "k"))
    Vault(store, local).set("a", "alpha-value")
    Vault(store, local).set("b", "beta-value")
    vault = Vault(store, approle(fake_openbao), [local])
    assert vault.status()["legacy_secrets"] == 2 and vault.get("a") == "alpha-value"
    assert vault.migrate() == {"migrated": 2, "unchanged": 0, "failed": 0}
    assert vault.migrate() == {"migrated": 0, "unchanged": 2, "failed": 0}
    assert vault.status()["legacy_secrets"] == 0
    assert Vault(store, approle(fake_openbao)).get("b") == "beta-value"  # no local key needed any more


def test_migration_back_to_file(tmp_path: Path, store: Store, fake_openbao: FakeOpenBao) -> None:
    bao = approle(fake_openbao)
    Vault(store, bao).set("a", "alpha-value")
    local = SecretBox(load_or_create_key(tmp_path / "k"))
    vault = Vault(store, local, [bao])
    assert vault.migrate()["migrated"] == 1
    assert Vault(store, local).get("a") == "alpha-value"


def test_migration_reports_unreadable_secrets_and_stops_on_outage(tmp_path: Path, store: Store, fake_openbao: FakeOpenBao) -> None:
    local = SecretBox(load_or_create_key(tmp_path / "k"))
    Vault(store, local).set("good", "g-value-1")
    store.secret_set("lost", SecretBox(b"\x07" * 32).encrypt("x", "lost"))  # encrypted under a key we no longer have
    vault = Vault(store, approle(fake_openbao), [local])
    assert vault.migrate() == {"migrated": 1, "unchanged": 0, "failed": 1}
    fake_openbao.sealed = True
    store.secret_set("again", local.encrypt("y", "again"))
    with pytest.raises(VaultUnavailable):
        vault.migrate()
    assert store.secret_get("again")[:1] == b"\x01"  # type: ignore[index]  # still readable with the old key


def test_blob_from_a_missing_backend_is_reported(tmp_path: Path, store: Store, fake_openbao: FakeOpenBao) -> None:
    Vault(store, approle(fake_openbao)).set("s", "v")
    local_only = Vault(store, SecretBox(load_or_create_key(tmp_path / "k")))
    assert local_only.get("s") is None  # logged: needs OpenBao, which is not configured


# Building services from the environment


def test_openbao_backend_never_writes_a_key_file(tmp_path: Path, fake_openbao: FakeOpenBao) -> None:
    svc = Services.open(settings_for(tmp_path, fake_openbao))
    try:
        assert svc.vault.backend == "openbao" and svc.vault.status()["ok"]
        assert not (tmp_path / "data" / ".secret.key").exists()
    finally:
        svc.close()


def test_backend_openbao_needs_credentials(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="needs AIFRED_OPENBAO_ADDR"):
        Services.open(settings_for(tmp_path, None))
    with pytest.raises(ValueError, match="token, or a role id and a secret id"):
        Services.open(settings_for(tmp_path / "b", None, openbao_addr="http://x:8200"))


def test_credentials_can_come_from_files(tmp_path: Path, fake_openbao: FakeOpenBao) -> None:
    secret_file = tmp_path / "secret_id"
    secret_file.write_text(fake_openbao.SECRET_ID + "\n")
    settings = settings_for(tmp_path, None, openbao_addr=fake_openbao.url, openbao_role_id=fake_openbao.ROLE_ID, openbao_secret_id_file=secret_file)
    svc = Services.open(settings)
    try:
        svc.vault.set("s", "v")
        assert svc.vault.get("s") == "v"
    finally:
        svc.close()
    secret_file.write_text("")
    with pytest.raises(ValueError, match="is empty"):
        Services.open(settings.model_copy(update={"data_dir": tmp_path / "other"}))
    with pytest.raises(ValueError, match="cannot read"):
        Services.open(settings.model_copy(update={"data_dir": tmp_path / "other2", "openbao_secret_id_file": tmp_path / "missing"}))


def test_start_with_openbao_down_still_works(tmp_path: Path, fake_openbao: FakeOpenBao) -> None:
    fake_openbao.sealed = True
    svc = Services.open(settings_for(tmp_path, fake_openbao))  # the dashboard must come up to show the problem
    try:
        assert svc.vault.status() == {"backend": "openbao", "ok": False, "detail": "sealed", "legacy_secrets": 0}
    finally:
        svc.close()


def test_legacy_key_file_is_read_but_not_created(tmp_path: Path, fake_openbao: FakeOpenBao, caplog: pytest.LogCaptureFixture) -> None:
    file_settings = settings_for(tmp_path, None, backend="file")
    first = Services.open(file_settings)
    first.vault.set("old", "old-secret")
    first.close()
    svc = Services.open(settings_for(tmp_path, fake_openbao))
    try:
        assert svc.vault.get("old") == "old-secret"  # still readable through the leftover key
        assert svc.vault.status()["legacy_secrets"] == 1 and ".secret.key still exists" in caplog.text
        assert svc.vault.migrate()["migrated"] == 1
    finally:
        svc.close()


# Behaviour during an outage


def llm_services(tmp_path: Path, bao: FakeOpenBao, fake: FakeLLM) -> Services:
    svc = Services.open(settings_for(tmp_path, bao))
    svc.bootstrap()
    p = svc.providers.create({"slug": "cloud", "type": "local", "base_url": fake.url}, "sk-cloud-key-0001")
    svc.providers.save_models(p.id, [{"model_id": "test-model"}])
    svc.store.model_set_test(p.id, "test-model", True)
    svc.llm.set_route("default", [{"provider": "cloud", "model": "test-model"}])
    return svc


async def test_sealed_openbao_never_invalidates_a_provider_key(tmp_path: Path, fake_openbao: FakeOpenBao, fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm()
    fake.api_key = "sk-cloud-key-0001"
    svc = llm_services(tmp_path, fake_openbao, fake)
    try:
        svc.vault._cache.clear()  # as after a restart: values must come from OpenBao again
        fake_openbao.sealed = True
        provider = svc.providers.by_slug("cloud")
        assert provider is not None
        entries = svc.llm.resolve("default")
        with pytest.raises(LLMError) as exc:
            await svc.llm.complete(entries, [{"role": "user", "content": "hi"}])
        assert exc.value.outcome is Outcome.FATAL and "secret store is unavailable" in str(exc.value)
        assert fake.requests == []  # nothing was sent to the provider without its key
        [key] = svc.store.key_list(provider.id)
        assert svc.store.health_get("key", str(key["id"])) is None  # not marked invalid
        assert svc.store.health_get("entry", f"{provider.id}|test-model") is None  # no cooldown either
        reply = await svc.agent.reply("cli:1", "hello", "cli")
        assert "secret store is unavailable" in reply
        fake_openbao.sealed = False
        fake.queue(Text("back again"))
        assert await svc.agent.reply("cli:2", "hello", "cli") == "back again"
        assert fake.requests[0].headers["authorization"] == "Bearer sk-cloud-key-0001"
    finally:
        svc.close()


async def test_writes_during_an_outage_leave_nothing_half_done(tmp_path: Path, fake_openbao: FakeOpenBao, fake_llm: Callable[[], FakeLLM]) -> None:
    svc = llm_services(tmp_path, fake_openbao, fake_llm())
    try:
        provider = svc.providers.by_slug("cloud")
        assert provider is not None
        fake_openbao.sealed = True
        with pytest.raises(ProviderError, match="not saved"):
            svc.providers.add_key(provider.id, "another-key-value")
        assert len(svc.store.key_list(provider.id)) == 1  # no orphan key row
        [key] = svc.store.key_list(provider.id)
        with pytest.raises(ProviderError, match="not replaced"):
            svc.providers.replace_key(key["id"], "replacement-key")
        with pytest.raises(SettingError, match="secret store is unavailable"):
            svc.config.update({"TELEGRAM_TOKEN": "123:abc-token-value"})
        assert not svc.vault.exists("setting:TELEGRAM_TOKEN")
        probe = svc.providers.preview({"slug": "x", "type": "local", "base_url": "http://127.0.0.1:9/v1"})
        svc.vault._cache.clear()
        with pytest.raises(ProviderError, match="secret store is unavailable"):
            await svc.providers.refresh_models(provider.id)
        del probe
    finally:
        svc.close()


def test_status_endpoint(tmp_path: Path, fake_openbao: FakeOpenBao) -> None:
    with TestClient(create_app(settings_for(tmp_path, fake_openbao)), base_url="https://testserver") as client:
        csrf = login(client)
        assert client.get("/api/system/vault").json() == {"backend": "openbao", "ok": True, "detail": "ready", "legacy_secrets": 0}
        fake_openbao.sealed = True
        assert client.get("/api/system/vault").json()["detail"] == "sealed"
        client.headers["X-CSRF-Token"] = csrf


def test_status_endpoint_needs_admin(client: TestClient) -> None:
    assert client.get("/api/system/vault").status_code == 401


# The migration command


def run_main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: dict[str, str], *args: str) -> int:
    from aifred.__main__ import main

    monkeypatch.chdir(tmp_path)  # never pick up a developer's .env
    for key in [k for k in os.environ if k.startswith("AIFRED_")]:
        monkeypatch.delenv(key)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return main(["admin", *args])


def test_migrate_secrets_command(tmp_path: Path, fake_openbao: FakeOpenBao, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    data = tmp_path / "data"
    old = Services.open(settings_for(tmp_path, None, backend="file"))
    old.vault.set("provider_key:1", "sk-to-be-moved-1234")
    old.close()
    env = {
        "AIFRED_DATA_DIR": str(data), "AIFRED_SECRETS_BACKEND": "openbao", "AIFRED_OPENBAO_ADDR": fake_openbao.url,
        "AIFRED_OPENBAO_ROLE_ID": fake_openbao.ROLE_ID, "AIFRED_OPENBAO_SECRET_ID": fake_openbao.SECRET_ID,
    }
    assert run_main(monkeypatch, tmp_path, env, "migrate-secrets") == 0
    out = capsys.readouterr().out
    assert "1 migrated" in out and "You can now delete" in out and str(data / ".secret.key") in out
    (data / ".secret.key").unlink()
    svc = Services.open(settings_for(tmp_path, fake_openbao))
    try:
        assert svc.vault.get("provider_key:1") == "sk-to-be-moved-1234"
    finally:
        svc.close()


def test_migrate_secrets_command_reports_an_outage(tmp_path: Path, fake_openbao: FakeOpenBao, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    old = Services.open(settings_for(tmp_path, None, backend="file"))
    old.vault.set("s", "v")
    old.close()
    fake_openbao.sealed = True
    env = {
        "AIFRED_DATA_DIR": str(tmp_path / "data"), "AIFRED_SECRETS_BACKEND": "openbao", "AIFRED_OPENBAO_ADDR": fake_openbao.url,
        "AIFRED_OPENBAO_ROLE_ID": fake_openbao.ROLE_ID, "AIFRED_OPENBAO_SECRET_ID": fake_openbao.SECRET_ID,
    }
    assert run_main(monkeypatch, tmp_path, env, "migrate-secrets") == 1
    assert "sealed" in capsys.readouterr().err
    assert run_main(monkeypatch, tmp_path, {"AIFRED_DATA_DIR": str(tmp_path / "d2"), "AIFRED_SECRETS_BACKEND": "openbao"}, "migrate-secrets") == 2
    assert "needs AIFRED_OPENBAO_ADDR" in capsys.readouterr().err


# Hardening


@pytest.mark.skipif(shutil.which("setpriv") is None or os.geteuid() != 0, reason="needs root to drop to an unprivileged user with setpriv")
def test_hardening_hides_the_service_from_its_own_children() -> None:
    """As an unprivileged user (like the container user), a child must not read the parent's environ."""
    import tempfile

    repo = Path(__file__).resolve().parent.parent
    workdir = Path(tempfile.mkdtemp(prefix="aifred-probe-"))
    workdir.chmod(0o755)  # pytest's own temp dir is private; the unprivileged probe must be able to read its script
    try:
        _hardening_probe(repo, workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _hardening_probe(repo: Path, tmp_path: Path) -> None:
    script = tmp_path / "probe.py"
    script.write_text(textwrap.dedent(f"""
        import os, subprocess, sys
        sys.path.insert(0, {str(repo)!r})
        if sys.argv[1] == "harden":
            from aifred.hardening import harden_process
            assert harden_process()
        out = subprocess.run(["bash", "-c", "cat /proc/$PPID/environ 2>&1 | tr '\\\\0' '\\\\n' | grep -c token-in-the-environment || true"],
                             capture_output=True, text=True).stdout.strip()
        print(out)
    """))
    script.chmod(0o755)

    def probe(mode: str) -> str:
        proc = subprocess.run(
            ["setpriv", "--reuid=65534", "--regid=65534", "--clear-groups", "/usr/bin/python3", str(script), mode],
            capture_output=True, text=True,
            env={"PATH": "/usr/bin:/bin", "PROBE_SECRET": "token-in-the-environment"},  # /proc shows the initial environment
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip()

    assert probe("plain") == "1"  # control: without hardening the child can read it
    assert probe("harden") == "0"  # with hardening it cannot


# Orphan reaping (the service is PID 1 in its container)


def test_reap_orphans_leaves_tracked_children_alone() -> None:
    from aifred import hardening

    with hardening.spawn_guard():
        mine = subprocess.Popen(["bash", "-c", "exit 7"])
        hardening.track(mine.pid)
    stray = subprocess.Popen(["bash", "-c", "exit 0"])  # a child nobody registered
    deadline = time.time() + 5
    while time.time() < deadline and hardening.reap_orphans() == 0:
        time.sleep(0.05)
    assert stray.poll() is None or stray.returncode in (0, None)  # reaped behind the Popen object's back
    assert mine.wait() == 7  # the owner still gets the real exit code
    hardening.untrack(mine.pid)


@pytest.mark.skipif(shutil.which("unshare") is None or os.geteuid() != 0, reason="needs root and unshare to become PID 1")
def test_as_pid_1_orphans_are_reaped_and_exit_codes_survive() -> None:
    import tempfile

    repo = Path(__file__).resolve().parent.parent
    script = Path(tempfile.mkdtemp(prefix="aifred-pid1-")) / "probe.py"
    script.write_text(textwrap.dedent(f"""
        import os, subprocess, sys, time
        sys.path.insert(0, {str(repo)!r})
        from aifred import hardening
        assert os.getpid() == 1, os.getpid()
        if sys.argv[1] == "reap":
            hardening.start_reaper(0.1)
        # a background job outlives the command that started it and is re-parented to us (PID 1)
        subprocess.run(["bash", "-c", "(sleep 0.3 &) ; exit 0"])
        with hardening.spawn_guard():
            child = subprocess.Popen(["bash", "-c", "sleep 0.6; exit 7"])
            hardening.track(child.pid)
        time.sleep(1.5)  # the orphan has exited by now; so has `child`, which we have not waited for yet
        zombies = 0
        for entry in os.listdir("/proc"):
            if entry.isdigit() and int(entry) != child.pid:
                try:
                    if open("/proc/%s/stat" % entry).read().rsplit(")", 1)[1].split()[0] == "Z":
                        zombies += 1
                except OSError:
                    pass
        print("zombies=%d exit=%d" % (zombies, child.wait()))
    """))

    def probe(mode: str) -> str:
        proc = subprocess.run(
            ["unshare", "--pid", "--fork", "--mount-proc", "--kill-child", "/usr/bin/python3", str(script), mode],
            capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip()

    try:
        assert probe("none") == "zombies=1 exit=7"  # control: nobody reaps, the orphan stays a zombie
        assert probe("reap") == "zombies=0 exit=7"  # the reaper collects it and leaves our child's exit code intact
    finally:
        shutil.rmtree(script.parent, ignore_errors=True)
