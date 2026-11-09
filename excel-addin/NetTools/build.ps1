<#
.SYNOPSIS
    Builds the NetTools Excel add-in (NetTools.xlam) and, optionally, its MSI installer.

.DESCRIPTION
    1. Starts Excel in the background, imports src\NetTools.bas into a new
       workbook and saves it as an add-in (.xlam).
    2. Adds the ribbon (src\customUI.xml) and the add-in's title/description
       to the .xlam package.
    3. With -Msi: packages the .xlam into dist\NetTools-<version>.msi with
       the WiX Toolset (installed automatically as a .NET tool if missing).

    Needs Windows with desktop Excel (2010 or later). Step 1 needs
    "Trust access to the VBA project object model"; the script switches it
    on for the duration of the build and puts it back afterwards.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\build.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\build.ps1 -Msi
#>
[CmdletBinding()]
param(
    [switch]$Msi,
    [string]$Version = (Get-Content (Join-Path $PSScriptRoot 'VERSION') -Raw).Trim()
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$srcDir   = Join-Path $PSScriptRoot 'src'
$distDir  = Join-Path $PSScriptRoot 'dist'
$basFile  = Join-Path $srcDir 'NetTools.bas'
$uiFile   = Join-Path $srcDir 'customUI.xml'
$xlamFile = Join-Path $distDir 'NetTools.xlam'
$wxsFile  = Join-Path $PSScriptRoot 'installer\NetTools.wxs'
$msiFile  = Join-Path $distDir "NetTools-$Version.msi"
$wixVersion = '5.0.2'

New-Item -ItemType Directory -Force -Path $distDir | Out-Null
if (Test-Path $xlamFile) { Remove-Item $xlamFile -Force }

function Release-Com([object]$o) {
    if ($null -ne $o) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($o) }
}

# ---------------------------------------------------------------------------
# 1. Build the .xlam with Excel
# ---------------------------------------------------------------------------
Write-Host "Building NetTools.xlam $Version ..."

# Excel's version (e.g. 16.0) tells us where its Trust Center settings live
$probe = New-Object -ComObject Excel.Application
$officeVer = $probe.Version
$probe.Quit(); Release-Com $probe; $probe = $null
[GC]::Collect(); [GC]::WaitForPendingFinalizers()

$secKey = "HKCU:\Software\Microsoft\Office\$officeVer\Excel\Security"
if (-not (Test-Path $secKey)) { New-Item -Path $secKey -Force | Out-Null }
$oldVbom = (Get-ItemProperty -Path $secKey -Name AccessVBOM -ErrorAction SilentlyContinue).AccessVBOM
Set-ItemProperty -Path $secKey -Name AccessVBOM -Value 1 -Type DWord

$excel = $null; $wb = $null
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.EnableEvents = $false
    $wb = $excel.Workbooks.Add()
    try {
        [void]$wb.VBProject.VBComponents.Import($basFile)
    } catch {
        throw "Excel refused access to the VBA project. In Excel: File > Options > Trust Center > " +
              "Trust Center Settings > Macro Settings, tick 'Trust access to the VBA project object model', " +
              "then run this script again. ($($_.Exception.Message))"
    }
    $wb.SaveAs($xlamFile, 55)   # 55 = xlOpenXMLAddIn
    $wb.Close($false)
} finally {
    if ($null -ne $excel) { $excel.Quit() }
    Release-Com $wb; Release-Com $excel
    $wb = $null; $excel = $null
    [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    if ($null -eq $oldVbom) {
        Remove-ItemProperty -Path $secKey -Name AccessVBOM -ErrorAction SilentlyContinue
    } else {
        Set-ItemProperty -Path $secKey -Name AccessVBOM -Value $oldVbom -Type DWord
    }
}

# ---------------------------------------------------------------------------
# 2. Add the ribbon XML and document properties to the package
# ---------------------------------------------------------------------------
function Read-ZipXml($zip, [string]$name) {
    $entry = $zip.GetEntry($name)
    if ($null -eq $entry) { return $null }
    $reader = New-Object IO.StreamReader($entry.Open())
    try { $xml = New-Object Xml.XmlDocument; $xml.LoadXml($reader.ReadToEnd()); return $xml }
    finally { $reader.Close() }
}

function Write-ZipText($zip, [string]$name, [string]$text) {
    $old = $zip.GetEntry($name)
    if ($null -ne $old) { $old.Delete() }
    $writer = New-Object IO.StreamWriter($zip.CreateEntry($name).Open(), (New-Object Text.UTF8Encoding($false)))
    try { $writer.Write($text) } finally { $writer.Close() }
}

Write-Host "Adding ribbon ..."
$zip = [IO.Compression.ZipFile]::Open($xlamFile, [IO.Compression.ZipArchiveMode]::Update)
try {
    Write-ZipText $zip 'customUI/customUI.xml' ([IO.File]::ReadAllText($uiFile))

    # Package relationship that points Office at the ribbon XML
    $relNs   = 'http://schemas.openxmlformats.org/package/2006/relationships'
    $relType = 'http://schemas.microsoft.com/office/2006/relationships/ui/extensibility'
    $rels = Read-ZipXml $zip '_rels/.rels'
    $exists = $false
    foreach ($r in $rels.DocumentElement.ChildNodes) { if ($r.GetAttribute('Type') -eq $relType) { $exists = $true } }
    if (-not $exists) {
        $node = $rels.CreateElement('Relationship', $relNs)
        $node.SetAttribute('Id', 'rIdNetToolsRibbon')
        $node.SetAttribute('Type', $relType)
        $node.SetAttribute('Target', 'customUI/customUI.xml')
        [void]$rels.DocumentElement.AppendChild($node)
    }
    Write-ZipText $zip '_rels/.rels' $rels.OuterXml

    # Title/description shown in Excel's Add-ins dialog
    $dcNs = 'http://purl.org/dc/elements/1.1/'
    $core = Read-ZipXml $zip 'docProps/core.xml'
    if ($null -ne $core) {
        $props = [ordered]@{ title = 'NetTools'; description = 'DNS lookup, reverse lookup and ping for a column of IP addresses or hostnames.' }
        foreach ($k in $props.Keys) {
            $n = $core.DocumentElement.GetElementsByTagName($k, $dcNs) | Select-Object -First 1
            if ($null -eq $n) { $n = $core.CreateElement('dc', $k, $dcNs); [void]$core.DocumentElement.AppendChild($n) }
            $n.InnerText = $props[$k]
        }
        Write-ZipText $zip 'docProps/core.xml' $core.OuterXml
    }
} finally {
    $zip.Dispose()
}
Write-Host "Built $xlamFile" -ForegroundColor Green

# ---------------------------------------------------------------------------
# 3. MSI (optional)
# ---------------------------------------------------------------------------
if (-not $Msi) { return }

# MSI versions must be numeric x.y.z
if ($Version -notmatch '^\d+\.\d+(\.\d+)?$') { throw "VERSION '$Version' must look like 1.0.0" }

$wix = Get-Command wix -ErrorAction SilentlyContinue
if ($null -eq $wix) {
    if ($null -eq (Get-Command dotnet -ErrorAction SilentlyContinue)) {
        throw "The MSI needs the WiX Toolset, which needs the .NET SDK (6 or later): https://dotnet.microsoft.com/download"
    }
    Write-Host "Installing WiX Toolset $wixVersion (dotnet tool) ..."
    dotnet tool install --global wix --version $wixVersion
    if ($LASTEXITCODE -ne 0) { throw "Could not install the WiX Toolset." }
    $env:PATH += ";$env:USERPROFILE\.dotnet\tools"
}

Write-Host "Building MSI ..."
if (Test-Path $msiFile) { Remove-Item $msiFile -Force }
wix build $wxsFile -arch x86 -d "Version=$Version" -d "XlamPath=$xlamFile" -o $msiFile
if ($LASTEXITCODE -ne 0) { throw "wix build failed." }
Remove-Item (Join-Path $distDir '*.wixpdb') -ErrorAction SilentlyContinue
Write-Host "Built $msiFile" -ForegroundColor Green
