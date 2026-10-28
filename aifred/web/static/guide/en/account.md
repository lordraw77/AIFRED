# Account

The dashboard's administrator account: change the password and see, or end, the browsers that are signed in.

![Password and signed-in sessions](/static/guide/img/account-1.png)

## What you see

- **Password**: *Current password*, *New password* twice, **Change password**. A successful change signs every other browser out.
- **Signed-in sessions**: each browser or device that is signed in: a description of the browser, its address, when it was last seen, a badge *this one* for the one you are using, and **Sign out** for the others.

## How to

- **Change the password** after the first start, and remove `AIFRED_ADMIN_PASSWORD` from the `.env` file: it is only used to create the account.
- **Get rid of a forgotten session**: *Sign out* next to it.

## Good to know

- Too many wrong passwords lock the sign-in for a while, per address.
- The session cookie is `HttpOnly` and the dashboard checks a token on every change, so another site cannot act for you.
- If you lose the password, run `docker compose exec aifred python -m aifred admin reset-password` on the server.
