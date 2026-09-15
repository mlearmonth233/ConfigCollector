# Packrat marketing site

A single static page (`index.html`) plus `assets/` (logo SVGs and product
screenshots). No build step, no framework, no tracking.

## Preview locally

```bash
cd website
python3 -m http.server 8080
# open http://localhost:8080
```

## Publish

Any static host works. Two zero-config options:

- **GitHub Pages**: Settings → Pages → "Deploy from a branch", folder
  `/website`. The site appears at `https://<user>.github.io/ConfigCollector/`.
- **Netlify / Cloudflare Pages**: drag the `website/` folder onto the
  dashboard, or point a project at this repo with publish directory
  `website`.

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

## Taking payments

The Colony and Warren buttons are `mailto:` links so you can sell by hand
first. When you want self-serve checkout, the smallest change is to swap
the Colony button's `href` for a Stripe Payment Link (create one in the
Stripe dashboard, no code needed) and leave Warren as a contact link.

## Refreshing the screenshots

They were taken from a seeded demo organization ("Northwind Utilities") at
1360×820, device scale factor 1.5, timezone America/Denver, so the numbers
and names are consistent across shots. If you retake them, keep the same
viewport so the gallery stays visually even.
