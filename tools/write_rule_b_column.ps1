# Write the Rule B Bucket column into "Working Sheet" with the installed Excel (COM).
# Called by tools/rule_b_bucket.py. Values file: UTF-8, one value per line for rows 2..Rows+1
# (empty line = leave the cell blank). The column is the next empty one in row 1, or the
# existing column whose header already equals -Header (rerun-safe). The header cell gets
# the formatting of the header cell to its left (Range.Copy, no clipboard). Plain text
# values only; no other cell is changed. Prints RULEB_OK <column letter> or RULEB_FAILED.
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$ValuesFile,
    [Parameter(Mandatory = $true)][int]$Rows,
    [Parameter(Mandatory = $true)][string]$Header
)
$ErrorActionPreference = "Stop"
$full = (Resolve-Path $Path).Path
if ([IO.Path]::GetExtension($full).ToLowerInvariant() -ne ".xlsx") {
    throw "Only .xlsx workbooks may be written"
}
$values = @()
if ($Rows -gt 0) { $values = [IO.File]::ReadAllText($ValuesFile, [Text.Encoding]::UTF8) -split "`n" }
$xl = New-Object -ComObject Excel.Application
$xl.Visible = $false
$xl.DisplayAlerts = $false
$xl.AskToUpdateLinks = $false
$xl.AutomationSecurity = 3 # msoAutomationSecurityForceDisable
$step = "open"
try {
    $wb = $xl.Workbooks.Open($full, 0, $false)
    $step = "find column"
    $ws = $wb.Worksheets.Item("Working Sheet")
    $lastCol = $ws.Cells.Item(1, $ws.Columns.Count).End(-4159).Column   # xlToLeft
    $col = 0
    for ($c = 1; $c -le $lastCol; $c++) {
        if ([string]$ws.Cells.Item(1, $c).Value2 -eq $Header) { $col = $c; break }
    }
    $step = "header"
    if ($col -eq 0) {
        $col = $lastCol + 1
        $null = $ws.Cells.Item(1, $lastCol).Copy($ws.Cells.Item(1, $col))  # header format
        $ws.Cells.Item(1, $col).Value2 = $Header
    }
    $step = "values"
    $letter = ([string]$ws.Cells.Item(1, $col).Address($false, $false)) -replace '\d', ''
    if ($Rows -gt 0) {
        # Clear this column's data cells first (rerun-safe), then write each non-blank value.
        # Cell by cell: a bulk object[,] assignment fails on the real workbook in PowerShell.
        $null = $ws.Range(("{0}2:{0}{1}" -f $letter, ($Rows + 1))).ClearContents()
        for ($i = 0; $i -lt $Rows; $i++) {
            $v = if ($i -lt $values.Length) { $values[$i].TrimEnd("`r") } else { "" }
            if ($v -ne "") { $ws.Cells.Item($i + 2, $col).Value2 = [string]$v }
        }
    }
    $step = "save"
    $wb.Save()
    $wb.Close($false)
    Write-Output "RULEB_OK $letter"
} catch {
    Write-Output ("RULEB_FAILED at " + $step + ": " + $_.Exception.Message + " | line " +
                  $_.InvocationInfo.ScriptLineNumber)
} finally {
    $xl.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($xl) | Out-Null
}
