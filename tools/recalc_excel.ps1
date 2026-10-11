# Recalculate a workbook with the installed Excel (COM) so formula results are stored in the file.
# Usage: powershell -File recalc_excel.ps1 -Path "C:\...\OOJ_Working_x.xlsx"
param([Parameter(Mandatory = $true)][string]$Path)
$ErrorActionPreference = "Stop"
$full = (Resolve-Path $Path).Path
if ([IO.Path]::GetExtension($full).ToLowerInvariant() -ne ".xlsx") {
    throw "Only generated .xlsx workbooks may be recalculated"
}
$xl = New-Object -ComObject Excel.Application
$xl.Visible = $false
$xl.DisplayAlerts = $false
$xl.AskToUpdateLinks = $false
$xl.AutomationSecurity = 3 # msoAutomationSecurityForceDisable
try {
    $wb = $xl.Workbooks.Open($full, 0, $false) # UpdateLinks=0, ReadOnly=false
    $xl.CalculateFullRebuild()
    $wb.Save()
    $wb.Close($false)
    Write-Output "RECALC_OK $full"
} finally {
    $xl.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($xl) | Out-Null
}
