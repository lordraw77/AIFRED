# OpenBao for AIFRED: integrated (Raft) storage on a single node, one plain-HTTP listener reachable
# only on the internal Docker network (the service publishes no port). To use TLS, mount a
# certificate and set tls_cert_file/tls_key_file here, switch AIFRED_OPENBAO_ADDR to https and set
# AIFRED_OPENBAO_CACERT. (The older "file" storage type no longer exists in OpenBao 2.7.)
ui            = false
log_level     = "info"
api_addr      = "http://openbao:8200"
cluster_addr  = "http://127.0.0.1:8201"

# mlock needs IPC_LOCK, which the container does not get (cap_drop: ALL), and Raft memory-maps its
# store. Keep swap disabled or encrypted on the host so decrypted material is never written to disk.
disable_mlock = true

storage "raft" {
  path    = "/openbao/file"
  node_id = "aifred"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = true

  # Break-glass only: uncomment to let scripts/openbao-root-token.sh create a root token when none
  # exists (OpenBao 2.5.3+ disables these endpoints by default). Comment it out again afterwards.
  # disable_unauthed_generate_root_endpoints = false
}

# Audit log of every request, kept on its own volume. Audit devices are declared here: OpenBao 2.7 no
# longer enables them through the API. If the device cannot write (disk full), requests are refused.
audit "file" "aifred" {
  description = "AIFRED audit log"
  options {
    file_path = "/openbao/logs/audit.log"
  }
}
