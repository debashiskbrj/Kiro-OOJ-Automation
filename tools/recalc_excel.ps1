# Recalculate a workbook with the installed Excel (COM) so formula results are stored in the file.
# Usage: powershell -File recalc_excel.ps1 -Path "C:\...\OOJ_Working_x.xlsx"
# Optional -PivotRows N (N = data rows in Working Sheet): also adds a "Remark Summary" sheet
# with a PivotTable counting each Remark (column P) value, largest first. Non-fatal: prints
# PIVOT_OK or PIVOT_FAILED <reason>; the recalculated workbook is saved either way.
param([Parameter(Mandatory = $true)][string]$Path, [int]$PivotRows = 0)
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
    if ($PivotRows -gt 0) {
        $sheetName = "Remark Summary"
        $ps = $null
        try {
            $ws = $wb.Worksheets.Item("Working Sheet")
            $remarkHeader = [string]$ws.Cells.Item(1, 16).Value2   # P = "Remark " (trailing space)
            $src = "'Working Sheet'!R1C1:R{0}C16" -f ($PivotRows + 1)  # A..P, header + data rows
            $ps = $wb.Worksheets.Add([Type]::Missing, $ws)            # right after Working Sheet
            $ps.Name = $sheetName
            $ps.Cells.Item(1, 1).Value2 = "Remark volume (Working Sheet column P). After editing P: right-click the table > Refresh."
            $pc = $wb.PivotCaches().Create(1, $src)                    # 1 = xlDatabase
            $pc.RefreshOnFileOpen = $true
            $pt = $pc.CreatePivotTable("'$sheetName'!R3C1", "RemarkPivot")
            $field = $pt.PivotFields($remarkHeader)
            $field.Orientation = 1                                     # xlRowField
            $tidHeader = [string]$ws.Cells.Item(1, 4).Value2           # D = "TID"
            $null = $pt.AddDataField($pt.PivotFields($tidHeader), "Volume", -4112)  # xlCount
            $field.AutoSort(2, "Volume")                               # 2 = xlDescending
            $ps.Columns.Item(1).ColumnWidth = 60
            $ws.Activate()                                             # open on Working Sheet
            Write-Output "PIVOT_OK"
        } catch {
            Write-Output ("PIVOT_FAILED " + $_.Exception.Message)
            if ($ps -ne $null) { try { $ps.Delete() } catch {} }
        }
    }
    $wb.Save()
    $wb.Close($false)
    Write-Output "RECALC_OK $full"
} finally {
    $xl.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($xl) | Out-Null
}
