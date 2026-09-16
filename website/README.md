# Packrat marketing site

A single static page (`index.html`) plus `assets/` (logo SVGs and product
screenshots). No build step, no framework, no tracking.

## Preview locally

```bash
cd website
python3 -m http.server 8080
# open http://localhost:8080
```

## What goes on the web host, and what does not

Only this folder is a website. The Packrat app itself (backend, worker,
scheduler, frontend) is not something to upload to a web host: it needs
long-running Python processes, Redis and a database, and above all it must
sit on a network that can reach the customer's switches over SSH. Shared
web hosting such as GoDaddy's cPanel plans provides none of that. The
product model the site describes is therefore: the site is public, and
each customer downloads and runs Packrat on a machine inside their own
network (the Windows run scripts, or Docker Compose on a server).

## Publish on GoDaddy (cPanel / Linux hosting)

1. **Domain and SSL first.** In the GoDaddy dashboard make sure the domain
   points at the hosting plan and that SSL is active for it (Web Hosting →
   Manage → Security → SSL; the managed certificate is included on most
   plans). `.htaccess` in this folder redirects every visitor to HTTPS, so
   without a certificate the site would show a warning.
2. **Package the site.** On your PC run `.\website\package-site.ps1`. It
   writes `website\packrat-site.zip` (about 3 MB) and refuses to run while
   the Stripe link is still `REPLACE_ME` (add `-AllowPlaceholders` to ship
   with the email fallback instead).
3. **Upload.** Web Hosting → Manage → cPanel Admin → **File Manager** →
   open `public_html`. Delete GoDaddy's placeholder files (`index.html`,
   `coming-soon` and the like) if present. Press **Upload**, choose the
   zip, then back in File Manager right-click it → **Extract** → into
   `public_html`. Delete the zip afterwards. Turn on *Settings → Show
   hidden files* to confirm `.htaccess` arrived.
4. **Check.** Open `https://yourdomain/` and `https://yourdomain/thanks.html`.
   Click "Start a 30-day trial" and confirm it opens Stripe (or your
   mailto fallback). On a phone too.
5. **Mailboxes.** The site uses `sales@` and `support@` on your domain
   (see below). Create them in GoDaddy (Email & Office, or cPanel → Email
   Accounts if the plan includes mail) or forward them to your own
   address, before anyone clicks.
6. **Updating later** is the same upload-and-extract; the HTML is cached
   for an hour at most.

FTP works too (cPanel → FTP Accounts; host is your domain, folder
`public_html`), and so does any other static host: GitHub Pages (Settings →
Pages → folder `/website`), Netlify or Cloudflare Pages (drag the folder
onto the dashboard).

## Before you go live

Search `index.html` for these and replace them:

- `sales@packrat.app` — the contact address on the pricing buttons and in
  the footer. Register the domain (or use one you already own) first.
- The **pricing tiers** (`#pricing`). The tiers, limits and prices are a
  starting proposal, not a decision. Nothing in the app enforces a device
  cap today; if you want one, it needs building.
- The **GitHub links** point at this repository. If you move the source or
  make it private, point "Download and run" at a release page instead.
- **Trademark check**: "Packrat" is used by unrelated software (an R
  package, a Chrome extension, a game). It is fine as a working name; do a
  proper clearance search before spending money on it.

## Taking payments (Stripe)

The Colony tier checks out through a Stripe Payment Link: Stripe hosts the
payment page, handles cards / Apple Pay / Google Pay, sends receipts and
invoices, and gives customers a billing portal to change card or cancel.
Nothing runs on your side, so the site stays static. Warren stays a
`mailto:` link because it is quoted per fleet. Until you paste a real link
the Colony button keeps its email fallback, so a half-configured deploy
never ships a dead Buy button.

Set-up, about twenty minutes in the Stripe dashboard:

1. **Create the product.** Products → Add product: "Packrat Colony",
   recurring, USD 79.00 monthly. Optional: add a yearly price too.
2. **Create the Payment Link.** Payment Links → New → pick the Colony
   price. In the link's options turn on:
   - *Free trial*: 30 days (matches the button text).
   - *Collect tax automatically* (Stripe Tax) if you sell outside your own
     tax jurisdiction, and *Collect customers' addresses* so tax is right.
   - *Allow promotion codes* if you plan to hand out discounts.
   - *Confirmation page*: "Don't show confirmation page, redirect
     customers to your website" with the URL
     `https://<your site>/thanks.html?session_id={CHECKOUT_SESSION_ID}`
     (keep the placeholder literally; Stripe fills it in).
3. **Paste the link.** In `index.html`, set `STRIPE_PAYMENT_LINK` in the
   script at the foot of the page to the `https://buy.stripe.com/...` URL.
4. **Turn on the billing portal.** Settings → Billing → Customer portal:
   allow customers to update payment methods and cancel. The link to it
   is included in Stripe's receipt emails automatically.
5. **Test it.** Stripe gives every link a test-mode twin. Paste the test
   link first, check out with card `4242 4242 4242 4242`, confirm you land
   on `thanks.html` with a reference shown, then swap in the live link.
6. **Emails.** Settings → Emails: turn on receipts for successful
   payments and set the support address; `thanks.html` and the FAQ point
   customers at `support@packrat.app`, so create that mailbox.

Stripe's fees come off each payment; nothing else is needed for a
subscription product with no per-customer fulfilment.

## Later: licence keys

Today every tier is the same download and device caps run on the honour
system. When you want the app itself to know a customer has paid, three
pieces are needed. The website side is already shaped for it:

1. **Issue a key on payment.** A Stripe webhook (`checkout.session.completed`)
   hits a small serverless function (Cloudflare Worker, Netlify or Vercel
   function). It generates a signed key carrying the tier, device limit
   and expiry, stores it against the Stripe customer id, and emails it.
   `thanks.html` receives the `session_id` in its URL and has a marked
   spot to show the key, fetched from that same function.
2. **Renewals and cancellations.** The same webhook handles
   `invoice.paid` (extend expiry) and `customer.subscription.deleted`
   (mark the key ended). Signed keys with an expiry mean the app can check
   validity offline; a monthly "phone home" is optional.
3. **In the app.** A Licence section on Settings where an admin pastes
   the key; the backend verifies the signature with an embedded public
   key and exposes tier and device limit. Whether the limit is a reminder
   or a hard stop is a product decision to make then.

Alternatively, Lemon Squeezy or Paddle act as merchant of record (they
handle VAT and sales tax worldwide) and Lemon Squeezy issues licence keys
out of the box; if tax handling becomes a burden, switching the Colony
button to one of their checkout links is the same one-line change.

## Refreshing the screenshots

They were taken from a seeded demo organization ("Northwind Utilities") at
1360×820, device scale factor 1.5, timezone America/Denver, so the numbers
and names are consistent across shots. If you retake them, keep the same
viewport so the gallery stays visually even.
