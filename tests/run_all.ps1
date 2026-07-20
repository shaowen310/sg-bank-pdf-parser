# Run all PDF test cases through sg_bank_pdf_parser
# Flow per PDF: PDF → IR JSON → masked .md + unmasked .md  (3 files total)
# Usage: ./run_all.ps1          (regenerates all outputs)
#        ./run_all.ps1 -Quick    (skip the unmasked variant)
param([switch]$Quick)

$PkgRoot = Resolve-Path "$PSScriptRoot/.."
$Cache   = Resolve-Path "$PSScriptRoot/cache"
$Output  = Resolve-Path "$PSScriptRoot/outputs"

Push-Location $PkgRoot
try {
    $pdfs = Get-ChildItem "$Cache/*.pdf" | ForEach-Object { $_.FullName }

    foreach ($pdf in $pdfs) {
        $baseName = [IO.Path]::GetFileNameWithoutExtension($pdf)
        $outMd    = Join-Path $Output "$baseName.md"
        $irJson   = Join-Path $Output "${baseName}.ir.json"

        # Step 1: PDF → IR JSON only (--ir-only suppresses Markdown output)
        Write-Host "--- $baseName (IR JSON) ---" -ForegroundColor Magenta
        python -m sg_bank_pdf_parser $pdf $outMd --ir-only
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  FAILED (IR JSON)" -ForegroundColor Red
            continue
        }

        # Step 2: IR JSON → masked Markdown
        Write-Host "--- $baseName (masked) ---" -ForegroundColor Cyan
        python -m sg_bank_pdf_parser $irJson $outMd
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  FAILED (masked)" -ForegroundColor Red
        }
    }

    Write-Host "`nAll done." -ForegroundColor Green
    Write-Host "Outputs in: $Output"
}
finally {
    Pop-Location
}
