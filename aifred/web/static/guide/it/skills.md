# Skills

Una skill è una procedura salvata: un breve documento Markdown che l'assistente può caricare quando un compito lo richiede ("come funziona il backup notturno", "scrivi il report settimanale dei server"). Nel prompt dell'assistente c'è solo la descrizione di una riga; il testo completo viene letto quando serve, quindi le skill costano quasi nulla finché non si usano.

![L'editor delle skill con l'anteprima aperta](/static/guide/img/skills-1-editor.png)

## Cosa vedi

- **Generate with AI** e **New skill**, accanto alla casella di ricerca. *Generate with AI* apre una casella dove descrivi in una frase cosa deve fare la skill; l'editor si apre poi con nome, descrizione e passi già scritti (vedi *Come fare*).
- **L'elenco**: una griglia di schede (nome in grassetto, descrizione sotto) con una **casella di ricerca** che filtra per nome e descrizione, e **New skill** accanto. Un clic su una scheda la apre.
- **L'editor**, a tutta larghezza, con **← All skills** sopra per tornare all'elenco:
  - **Name**: lettere minuscole, cifre, `-` e `_`. Dopo il salvataggio non si può cambiare.
  - **Description**: una riga che dice *quando l'assistente deve usarla*. È ciò che legge per scegliere. Sotto il campo, **Generate** e **Improve** chiedono al modello di scrivere o riscrivere la descrizione a partire dal testo della skill; vedi una bozza e scegli *Use this text* o *Discard*.
  - **Edit / Preview**: scrivi il corpo in Markdown, oppure guarda come appare.
  - **Save** e **Delete** (con conferma).

## Come fare

**Generarne una:** premi *Generate with AI*, descrivi il lavoro ("controlla perché un sito Nginx risponde 502 e proponi la correzione minima") e premi *Generate*. L'editor si apre con una bozza e una nota che dice quale modello l'ha scritta. Leggila, correggi ciò che non va e premi *Save*: prima non viene salvato nulla, e un nome già in uso riceve un numero in coda.

**Oppure scrivila tu:**

1. *New skill*, dalle un nome.
2. Scrivi la descrizione come un innesco: "Usala quando l'utente chiede come ripristinare un file dal backup del NAS".
3. Scrivi i passaggi nel corpo, come li spiegheresti a un collega.
4. *Save*. L'assistente la vede dalla conversazione successiva (oppure premi *Apply to open conversations* nella pagina [Tools](#/guide?s=tools)).

## Da sapere

- **Anche l'assistente può scrivere skill.** Dopo aver risolto qualcosa di complesso può salvarne una (strumento `skill_save`); con `WRITE_APPROVAL` attivo aspetta in [Journey](#/guide?s=journey). Non può mai cancellare una skill: solo tu.
- **Le skill sono file** nella cartella `skills` dei dati, quindi sono incluse nei tuoi backup.
- **I sub-agenti** possono avere skill precaricate nel prompt: vedi [Agents](#/guide?s=agents).
- **Sicurezza.** Il testo delle skill viene analizzato come la memoria: istruzioni iniettate e caratteri nascosti sono rifiutati.
