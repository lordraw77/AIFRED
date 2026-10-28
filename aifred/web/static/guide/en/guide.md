# This guide

The guide you are reading is part of the dashboard, so it always matches the version you run.

![The guide page](/static/guide/img/guide-1.png)

## How it works

- **The list on the left** has one section for each page of the dashboard, plus *Getting started* and *Chat commands and Telegram*.
- **English / Italiano** at the top of the list switches the language; the choice is remembered. The first time the guide opens in the language of your browser.
- **The *Guide* button** at the top right of every page opens the section about that page.
- **Previous / Next** at the bottom of a section walk through the guide in order.
- Pictures open full size in a new tab when you click them.
- The pictures show the dashboard in English; the Italian text describes the same buttons by their English names.

## For developers

The guide is made of Markdown files under `aifred/web/static/guide/` (`en/` and `it/`, one file per section, listed in `index.json`) and pictures in `img/`. The pictures are taken from a demo instance with invented data by the scripts in `scripts/guide-screenshots/`, so they can be redone whenever the dashboard changes.
