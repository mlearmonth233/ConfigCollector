<#
.SYNOPSIS
  Zips the marketing site for upload to a web host (GoDaddy cPanel File
  Manager > Upload, then Extract into public_html).

.DESCRIPTION
  Produces packrat-site.zip next to this script containing index.html,
  thanks.html, .htaccess and assets/. Refuses to package while the Stripe
  Payment Link is still the REPLACE_ME placeholder unless -AllowPlaceholders
  is given, so a half-configured site is not shipped by accident.

.EXAMPLE
  .\website\package-site.ps1
  .\website\package-site.ps1 -AllowPlaceholders   # site without payments yet
#>
param([switch]$AllowPlaceholders)

$ErrorActionPreference = "Stop"
$site = $PSScriptRoot
$out = Join-Path $site "packrat-site.zip"

$index = Get-Content (Join-Path $site "index.html") -Raw
if ($index -match 'buy\.stripe\.com/REPLACE_ME' -and -not $AllowPlaceholders) {
    Write-Host "index.html still has the placeholder Stripe Payment Link (REPLACE_ME)."
    Write-Host "Paste your https://buy.stripe.com/... link into STRIPE_PAYMENT_LINK, or run with -AllowPlaceholders to ship with the email fallback."
    exit 1
}
foreach ($needle in @("admin@milnetworkslimited.co.uk")) {
    if ($index -match [regex]::Escape($needle)) {
        Write-Host "Note: the site points customers at $needle - make sure that mailbox is receiving mail."
    }
}

if (Test-Path $out) { Remove-Item $out }
$staging = Join-Path ([System.IO.Path]::GetTempPath()) ("packrat-site-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $staging | Out-Null
try {
    foreach ($item in @("index.html", "thanks.html", ".htaccess", "assets")) {
        Copy-Item (Join-Path $site $item) -Destination $staging -Recurse
    }
    # Compress-Archive skips dotfiles when given a folder, so add .htaccess explicitly.
    Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $out
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::Open($out, "Update")
    try {
        if (-not ($zip.Entries | Where-Object { $_.FullName -eq ".htaccess" })) {
            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, (Join-Path $staging ".htaccess"), ".htaccess") | Out-Null
        }
    } finally { $zip.Dispose() }
} finally {
    Remove-Item $staging -Recurse -Force
}
$size = [math]::Round((Get-Item $out).Length / 1MB, 1)
Write-Host "Wrote $out ($size MB). Upload it in cPanel > File Manager > public_html > Upload, then right-click > Extract."
