# Skills

Una skill è una procedura salvata: un breve documento Markdown che l'assistente può caricare quando un compito lo richiede ("come funziona il backup notturno", "scrivi il report settimanale dei server"). Nel prompt dell'assistente c'è solo la descrizione di una riga; il testo completo viene letto quando serve, quindi le skill costano quasi nulla finché non si usano.

![L'editor delle skill con l'anteprima aperta](/static/guide/img/skills-1-editor.png)

## Cosa vedi

- **New skill** (in alto a sinistra) e **l'elenco delle skill**: nome in grassetto, descrizione sotto. Un clic la apre.
- **L'editor** a destra:
  - **Name**: lettere minuscole, cifre, `-` e `_`. Dopo il salvataggio non si può cambiare.
  - **Description**: una riga che dice *quando l'assistente deve usarla*. È ciò che legge per scegliere. Sotto il campo, **Generate** e **Improve** chiedono al modello di scrivere o riscrivere la descrizione a partire dal testo della skill; vedi una bozza e scegli *Use this text* o *Discard*.
  - **Edit / Preview**: scrivi il corpo in Markdown, oppure guarda come appare.
  - **Save** e **Delete** (con conferma).

## Come fare

1. *New skill*, dalle un nome.
2. Scrivi la descrizione come un innesco: "Usala quando l'utente chiede come ripristinare un file dal backup del NAS".
3. Scrivi i passaggi nel corpo, come li spiegheresti a un collega.
4. *Save*. L'assistente la vede dalla conversazione successiva (oppure premi *Apply to open conversations* nella pagina [Tools](#/guide?s=tools)).

## Da sapere

- **Anche l'assistente può scrivere skill.** Dopo aver risolto qualcosa di complesso può salvarne una (strumento `skill_save`); con `WRITE_APPROVAL` attivo aspetta in [Journey](#/guide?s=journey). Non può mai cancellare una skill: solo tu.
- **Le skill sono file** nella cartella `skills` dei dati, quindi sono incluse nei tuoi backup.
- **I sub-agenti** possono avere skill precaricate nel prompt: vedi [Agents](#/guide?s=agents).
- **Sicurezza.** Il testo delle skill viene analizzato come la memoria: istruzioni iniettate e caratteri nascosti sono rifiutati.
