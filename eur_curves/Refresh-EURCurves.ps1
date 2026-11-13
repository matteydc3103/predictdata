<#
Daily EURCurves_Demo refresh + closing FRA snapshot.

  1. Refresh the Initialization tab, then the Grp tab (straight away).
  2. Wait until the snap time (17:15), then refresh the Fras tab.
  3. Copy Fras!P3:AB12803 as values into a new workbook saved as
     "<SnapFolder>\Closing Snap dd-MM-yyyy.xlsx".

Run it once a day via Run-EURCurves.bat and leave the window open until it finishes.

  -FrasOnly   skip step 1 (e.g. to re-take the snap)
  -SnapNow    do not wait for the snap time
#>
param(
    [string]$WorkbookFolder = 'C:\Users\n527049\OneDrive - Santander Office 365\Documentos',
    [string]$WorkbookName   = 'EURCurves_Demo',              # without extension
    [string]$SnapFolder     = 'C:\Users\n527049\OneDrive - Santander Office 365\Documentos\Fra Snap',
    [string]$InitSheet      = 'Initialization',
    [string]$GrpSheet       = 'Grp',
    [string]$FrasSheet      = 'Fras',
    [string]$SnapRange      = 'P3:AB12803',
    [string]$SnapTime       = '17:15',                       # 24h local time
    [int]$TimeoutMinutes    = 90,                            # max wait per tab refresh
    # Optional: name of a macro that refreshes the active sheet, run before the recalculation.
    # e.g. 'RefreshEntireWorksheet' for Bloomberg. Leave empty to just recalculate the tab.
    [string]$RefreshMacro   = '',
    [switch]$FrasOnly,
    [switch]$SnapNow
)

$ErrorActionPreference = 'Stop'

function Log([string]$msg) {
    Write-Host ("[{0:HH:mm:ss}] {1}" -f (Get-Date), $msg)
}

# Retry a COM call while Excel is busy (calculating / in a dialog / cell being edited).
function Invoke-Excel([scriptblock]$action, [int]$retries = 60) {
    for ($i = 0; ; $i++) {
        try { return & $action }
        catch {
            if ($i -ge $retries) { throw }
            Start-Sleep -Seconds 2
        }
    }
}

function Find-Workbook($app) {
    foreach ($wb in $app.Workbooks) {
        if ($wb.Name -like "$WorkbookName.xls*" -or $wb.Name -ieq $WorkbookName) { return $wb }
    }
    return $null
}

# Attach to the Excel the user already has open (so add-ins are loaded). If the workbook is not
# open, open it the normal way (as if double-clicked) and then attach.
function Get-ExcelWorkbook {
    $app = $null
    try { $app = [Runtime.InteropServices.Marshal]::GetActiveObject('Excel.Application') } catch {}
    if ($app) {
        $wb = Invoke-Excel { Find-Workbook $app } 5
        if ($wb) { Log "Using already-open workbook $($wb.Name)"; return $wb }
    }

    $file = Get-ChildItem -LiteralPath $WorkbookFolder -Filter "$WorkbookName.xls*" |
            Where-Object { $_.Name -notlike '~$*' } | Select-Object -First 1
    if (-not $file) { throw "Cannot find $WorkbookName.xls* in $WorkbookFolder" }

    Log "Opening $($file.FullName)"
    Start-Process -FilePath $file.FullName
    $deadline = (Get-Date).AddMinutes(5)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        try {
            $app = [Runtime.InteropServices.Marshal]::GetActiveObject('Excel.Application')
            $wb = Find-Workbook $app
            if ($wb) { Start-Sleep -Seconds 15; return $wb }   # give add-ins time to load
        } catch {}
    }
    throw "Opened $($file.Name) but could not attach to it in Excel within 5 minutes."
}

function Get-Sheet($wb, [string]$name) {
    $names = @()
    foreach ($ws in $wb.Worksheets) {
        if ($ws.Name.Trim() -ieq $name) { return $ws }
        $names += $ws.Name
    }
    throw "Tab '$name' not found. Tabs in workbook: $($names -join ', ')"
}

# Wait until Excel has finished calculating and no cell on the tab is still waiting for data.
function Wait-SheetReady($app, $ws) {
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    $stable = 0
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        try {
            $calcDone = ($app.CalculationState -eq 0)                                         # xlDone
            $pending  = $ws.UsedRange.Find('Requesting Data', [Type]::Missing, -4163, 2)      # xlValues, xlPart
        } catch { $stable = 0; continue }                                                     # Excel busy
        if ($calcDone -and $null -eq $pending) {
            $stable++
            if ($stable -ge 2) { return }
        } else { $stable = 0 }
    }
    throw "Tab '$($ws.Name)' did not finish refreshing within $TimeoutMinutes minutes."
}

function Refresh-Sheet($wb, [string]$name) {
    $app = $wb.Application
    $ws  = Get-Sheet $wb $name
    $sw  = [Diagnostics.Stopwatch]::StartNew()
    Log "Refreshing '$($ws.Name)' ..."

    Invoke-Excel { $wb.Activate(); $ws.Activate() } | Out-Null

    if ($RefreshMacro) { Invoke-Excel { $app.Run($RefreshMacro) } | Out-Null }

    # Any data-connection tables on the tab
    foreach ($qt in $ws.QueryTables) { try { $qt.Refresh($false) | Out-Null } catch { Log "  query refresh failed: $_" } }
    foreach ($lo in $ws.ListObjects) { try { $lo.QueryTable.Refresh($false) | Out-Null } catch {} }

    # Recalculate this tab only (same as Shift+F9)
    Invoke-Excel { $ws.Calculate() } | Out-Null

    Wait-SheetReady $app $ws
    Log ("'{0}' done in {1:n1} min" -f $ws.Name, $sw.Elapsed.TotalMinutes)
}

function Save-Snapshot($wb) {
    $app = $wb.Application
    $ws  = Get-Sheet $wb $FrasSheet
    if (-not (Test-Path -LiteralPath $SnapFolder)) { New-Item -ItemType Directory -Path $SnapFolder | Out-Null }
    $snapPath = Join-Path $SnapFolder ("Closing Snap {0:dd-MM-yyyy}.xlsx" -f (Get-Date))

    Log "Copying $($ws.Name)!$SnapRange to $snapPath"
    $snapWb = Invoke-Excel { $app.Workbooks.Add() }
    $prevAlerts = $app.DisplayAlerts
    try {
        $dest = $snapWb.Worksheets.Item(1).Range('A1')
        Invoke-Excel { $ws.Range($SnapRange).Copy() | Out-Null; $dest.PasteSpecial(12) | Out-Null } | Out-Null  # values + number formats
        $app.CutCopyMode = 0
        $snapWb.Worksheets.Item(1).Name = 'Closing Snap'
        $app.DisplayAlerts = $false                      # overwrite if today's file already exists
        Invoke-Excel { $snapWb.SaveAs($snapPath, 51) } | Out-Null   # 51 = .xlsx
    } finally {
        $app.DisplayAlerts = $prevAlerts
        try { $snapWb.Close($false) } catch {}
    }
    Log "Saved $snapPath"
}

# ---------------------------------------------------------------------------------------------
try {
    $wb = Get-ExcelWorkbook

    if (-not $FrasOnly) {
        Refresh-Sheet $wb $InitSheet
        Refresh-Sheet $wb $GrpSheet
    }

    if (-not $SnapNow) {
        $target = [datetime]::ParseExact($SnapTime, 'HH:mm', [Globalization.CultureInfo]::InvariantCulture)
        if ((Get-Date) -lt $target) {
            Log "Waiting until $SnapTime to refresh '$FrasSheet'. Leave this window and Excel open."
            while ((Get-Date) -lt $target) { Start-Sleep -Seconds 20 }
        } else {
            Log "It is already past $SnapTime - refreshing '$FrasSheet' now."
        }
    }

    $wb = Get-ExcelWorkbook        # re-attach in case Excel was restarted during the day
    Refresh-Sheet $wb $FrasSheet
    Save-Snapshot $wb
    Log 'All done.'
}
catch {
    Log "ERROR: $($_.Exception.Message)"
    exit 1
}
