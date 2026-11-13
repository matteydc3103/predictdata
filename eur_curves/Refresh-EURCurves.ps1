<#
Daily EURCurves_Demo refresh + closing FRA snapshot.

  1. Refresh the Initialization tab, then the Grp tab (straight away).
  2. Wait until the snap time (17:15), then refresh the Fras tab.
  3. Copy Fras!P3:AB12803 as values into a new workbook saved as
     "<SnapFolder>\Closing Snap dd-MM-yyyy.xlsx".

Run it once a day via Run-EURCurves.bat and leave the window open until it finishes.

  -FrasOnly   skip step 1 (e.g. to re-take the snap)
  -SnapNow    do not wait for the snap time

Compare mode (run via Compare-Fras.bat):
  -Compare [-Date1 dd-MM-yyyy] [-Date2 dd-MM-yyyy]
  Builds a new workbook with one tab per date ("fra snap dd-MM-yyyy", earlier date first) from the
  saved closing snaps. With only one date, the second tab is the live Fras tab, refreshed now.
  Dates are asked for if not given.
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
    [switch]$SnapNow,
    [switch]$Compare,
    [string]$Date1 = '',
    [string]$Date2 = ''
)

$ErrorActionPreference = 'Stop'

function Log([string]$msg) {
    $line = "[{0:dd-MM-yyyy HH:mm:ss}] {1}" -f (Get-Date), $msg
    Write-Host $line
    try {   # also keep a log file, so unattended (scheduled) runs can be checked afterwards
        if (-not (Test-Path -LiteralPath $SnapFolder)) { New-Item -ItemType Directory -Path $SnapFolder | Out-Null }
        Add-Content -LiteralPath (Join-Path $SnapFolder 'EURCurves refresh log.txt') -Value $line
    } catch {}
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
    $stable = 0; $errors = 0; $lastNote = Get-Date
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        $why = ''
        try {
            # Only "calculating" (1) counts as busy. "Pending" (2) is normal in manual-calculation
            # workbooks, where the other tabs are still waiting to be recalculated.
            if ($app.CalculationState -eq 1) { $why = 'Excel is still calculating' }
            elseif ($null -ne $ws.UsedRange.Find('Requesting Data', [Type]::Missing, -4163, 2)) {   # xlValues, xlPart
                $why = 'cells are still showing "Requesting Data"'
            }
            $errors = 0
        } catch {
            $errors++; $why = "Excel is busy ($($_.Exception.Message))"
            if ($errors -ge 60) { throw "Cannot check tab '$($ws.Name)': $($_.Exception.Message)" }
        }
        if (-not $why) {
            $stable++
            if ($stable -ge 2) { return }
        } else {
            $stable = 0
            if (((Get-Date) - $lastNote).TotalMinutes -ge 2) { Log "  still waiting on '$($ws.Name)': $why"; $lastNote = Get-Date }
        }
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

# Paste a range as values + number formats at A1 of a sheet.
function Copy-Values($app, $srcRange, $destSheet) {
    Invoke-Excel { $srcRange.Copy() | Out-Null; $destSheet.Range('A1').PasteSpecial(12) | Out-Null } | Out-Null
    $app.CutCopyMode = 0
    try { $destSheet.Columns.AutoFit() | Out-Null } catch {}
}

function Save-Snapshot($wb) {
    $app = $wb.Application
    $ws  = Get-Sheet $wb $FrasSheet
    if (-not (Test-Path -LiteralPath $SnapFolder)) { New-Item -ItemType Directory -Path $SnapFolder | Out-Null }
    $snapPath = Get-SnapPath (Get-Date)

    Log "Copying $($ws.Name)!$SnapRange to $snapPath"
    $snapWb = Invoke-Excel { $app.Workbooks.Add() }
    $prevAlerts = $app.DisplayAlerts
    try {
        Copy-Values $app $ws.Range($SnapRange) $snapWb.Worksheets.Item(1)
        $snapWb.Worksheets.Item(1).Name = 'Closing Snap'
        $app.DisplayAlerts = $false                      # overwrite if today's file already exists
        Invoke-Excel { $snapWb.SaveAs($snapPath, 51) } | Out-Null   # 51 = .xlsx
    } finally {
        $app.DisplayAlerts = $prevAlerts
        try { $snapWb.Close($false) } catch {}
    }
    Log "Saved $snapPath"
}

# ----------------------------------- compare mode --------------------------------------------
function Get-SnapPath([datetime]$d) {
    Join-Path $SnapFolder ("Closing Snap {0:dd-MM-yyyy}.xlsx" -f $d)
}

function Parse-SnapDate([string]$text) {
    $formats = [string[]]@('d-M-yyyy', 'd/M/yyyy', 'd.M.yyyy', 'd-M-yy', 'd/M/yy', 'ddMMyyyy', 'yyyy-MM-dd')
    $d = [datetime]::MinValue
    $ok = [datetime]::TryParseExact($text.Trim(), $formats, [Globalization.CultureInfo]::InvariantCulture,
                                    [Globalization.DateTimeStyles]::None, [ref]$d)
    if (-not $ok) { throw "Could not read '$text' as a date. Use dd-mm-yyyy, e.g. 14-11-2026." }
    return $d
}

# Stop early, with the list of dates that do exist, if there is no snap for the date.
function Assert-SnapExists([datetime]$d) {
    if (Test-Path -LiteralPath (Get-SnapPath $d)) { return }
    $have = @(Get-ChildItem -LiteralPath $SnapFolder -Filter 'Closing Snap *.xlsx' -ErrorAction SilentlyContinue |
        ForEach-Object { try { Parse-SnapDate ($_.BaseName -replace '^Closing Snap ', '') } catch {} } |
        Sort-Object -Descending | Select-Object -First 10 | ForEach-Object { $_.ToString('dd-MM-yyyy') })
    $list = if ($have.Count) { "Most recent snaps available: $($have -join ', ')" } else { "There are no snaps in $SnapFolder yet." }
    throw ("No closing snap saved for {0:dd-MM-yyyy}. {1}" -f $d, $list)
}

function Copy-FromSnapFile($app, [datetime]$d, $destSheet) {
    $src = Invoke-Excel { $app.Workbooks.Open((Get-SnapPath $d), 0, $true) }      # no link update, read-only
    try {
        $s = $src.Worksheets.Item(1)
        $shape = $s.Range($SnapRange)                                             # same size as the snap range
        Copy-Values $app $s.Range('A1').Resize($shape.Rows.Count, $shape.Columns.Count) $destSheet
    } finally {
        try { $src.Close($false) } catch {}
    }
}

function Invoke-Compare {
    if (-not $Date1) {
        $Date1 = Read-Host 'First date  (dd-mm-yyyy)'
        $Date2 = Read-Host 'Second date (dd-mm-yyyy, or just press Enter to compare against LIVE rates)'
    }
    $x = Parse-SnapDate $Date1
    $y = $null
    if ($Date2.Trim()) {
        $y = Parse-SnapDate $Date2
        if ($y -eq $x) { throw 'The two dates are the same.' }
        if ($y -lt $x) { $x, $y = $y, $x }                 # earlier date always goes first
    }
    Assert-SnapExists $x
    if ($y) { Assert-SnapExists $y }

    $live = $null
    if ($y) {
        try { $app = [Runtime.InteropServices.Marshal]::GetActiveObject('Excel.Application') }
        catch { $app = New-Object -ComObject Excel.Application }
        $app.Visible = $true
    } else {
        $live = Get-ExcelWorkbook
        $app  = $live.Application
        Refresh-Sheet $live $FrasSheet
    }

    $out = Invoke-Excel { $app.Workbooks.Add() }
    $prevAlerts = $app.DisplayAlerts
    $app.DisplayAlerts = $false
    try {
        while ($out.Worksheets.Count -lt 2) { $out.Worksheets.Add([Type]::Missing, $out.Worksheets.Item($out.Worksheets.Count)) | Out-Null }
        while ($out.Worksheets.Count -gt 2) { $out.Worksheets.Item($out.Worksheets.Count).Delete() }
        $tab1 = $out.Worksheets.Item(1)
        $tab2 = $out.Worksheets.Item(2)

        # Excel does not allow "/" in tab names, so the date is written with dashes.
        $tab1.Name = "fra snap {0:dd-MM-yyyy}" -f $x
        Log "Copying closing snap $($x.ToString('dd-MM-yyyy')) to tab 1"
        Copy-FromSnapFile $app $x $tab1

        if ($y) {
            $tab2.Name = "fra snap {0:dd-MM-yyyy}" -f $y
            Log "Copying closing snap $($y.ToString('dd-MM-yyyy')) to tab 2"
            Copy-FromSnapFile $app $y $tab2
            $label = $y.ToString('dd-MM-yyyy')
        } else {
            $tab2.Name = "fra snap {0:dd-MM-yyyy} live" -f (Get-Date)
            Log "Copying live $FrasSheet!$SnapRange to tab 2"
            Copy-Values $app (Get-Sheet $live $FrasSheet).Range($SnapRange) $tab2
            $label = "Live {0:dd-MM-yyyy HHmm}" -f (Get-Date)
        }

        $folder = Join-Path $SnapFolder 'Comparisons'
        if (-not (Test-Path -LiteralPath $folder)) { New-Item -ItemType Directory -Path $folder | Out-Null }
        $path = Join-Path $folder ("Fra Compare {0:dd-MM-yyyy} vs {1}.xlsx" -f $x, $label)
        Invoke-Excel { $out.SaveAs($path, 51) } | Out-Null
        Invoke-Excel { $out.Activate(); $tab1.Activate() } | Out-Null
        Log "Saved $path (left open in Excel)"
    } finally {
        $app.DisplayAlerts = $prevAlerts
    }
}

# ---------------------------------------------------------------------------------------------
try {
    if ($Compare -or $Date1) { Invoke-Compare; Log 'All done.'; exit 0 }

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
    Log "ERROR: $($_.Exception.Message)  [script line $($_.InvocationInfo.ScriptLineNumber)]"
    exit 1
}
