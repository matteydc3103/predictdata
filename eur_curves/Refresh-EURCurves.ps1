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
    [int]$TimeoutMinutes    = 240,                           # max wait per tab refresh
    # Optional: name of a macro that refreshes the active sheet, run before the recalculation.
    # e.g. 'RefreshEntireWorksheet' for Bloomberg. Leave empty to just recalculate the tab.
    [string]$RefreshMacro   = '',
    # Sensitivity label put on every file the script saves. The label's ID is picked up automatically
    # from EURCurves_Demo (or an earlier snap) when that file carries a label with this name.
    # If it does not, set the ID here - run the script with -ShowLabel "<path to a file labelled Internal>" to get it.
    [string]$SensitivityLabelName = 'Internal',
    [string]$SensitivityLabelId   = '',
    [string]$SensitivitySiteId    = '',
    [string]$ShowLabel            = '',
    [switch]$DirtyCellsOnly,                                 # only recalc changed cells (plain Shift+F9) instead of the whole tab
    [int]$AddInWaitSeconds  = 30,                            # pause after opening, for data add-ins to connect
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

# Get the workbook. If it is already open in Excel, use that. Otherwise start Excel ourselves,
# load the installed add-ins (Excel does not load them on its own when started by a script) and
# open the workbook. Nothing needs to be open beforehand.
function Get-ExcelWorkbook {
    if ($script:CachedWb) {                                   # workbook we already found in this run
        try { $null = $script:CachedWb.Name; return $script:CachedWb } catch { $script:CachedWb = $null }
    }

    try {
        $app = [Runtime.InteropServices.Marshal]::GetActiveObject('Excel.Application')
        $wb  = Invoke-Excel { Find-Workbook $app } 5
        if ($wb) { Log "Using already-open workbook $($wb.Name)"; $script:CachedWb = $wb; return $wb }
        Log "Excel is running but $WorkbookName is not open in it - starting a separate Excel."
    } catch {
        Log "No running Excel found - starting Excel."
    }

    $file = Get-ChildItem -LiteralPath $WorkbookFolder -Filter "$WorkbookName.xls*" |
            Where-Object { $_.Name -notlike '~$*' } | Select-Object -First 1
    if (-not $file) { throw "Cannot find $WorkbookName.xls* in $WorkbookFolder" }

    $app = New-Object -ComObject Excel.Application
    $app.Visible = $true
    $app.UserControl = $true                                  # keep Excel open after the script ends
    $app.DisplayAlerts = $false
    try {
        # Re-tick every installed add-in so it actually loads (needs a workbook open to work).
        $tmp = $app.Workbooks.Add()
        foreach ($ai in $app.AddIns) {
            try {
                if ($ai.Installed) { $ai.Installed = $false; $ai.Installed = $true; Log "  loaded add-in $($ai.Name)" }
            } catch { Log "  could not load add-in $($ai.Name): $($_.Exception.Message)" }
        }
        $tmp.Close($false)

        Log "Opening $($file.FullName)"
        $wb = $app.Workbooks.Open($file.FullName)
    } finally {
        $app.DisplayAlerts = $true
    }
    Log "Giving add-ins $AddInWaitSeconds seconds to connect ..."
    Start-Sleep -Seconds $AddInWaitSeconds
    $script:CachedWb = $wb
    return $wb
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
                $ws.Calculate()                   # in manual mode, arrived data only shows after a recalc
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

    # Recalculate this tab only.
    $prevKey = $app.CalculationInterruptKey
    try {
        $app.CalculationInterruptKey = 0          # a key press / click must not abort a long calculation
        if (-not $DirtyCellsOnly) {
            # Mark every formula on the tab as needing recalculation. A plain Shift+F9 only redoes
            # cells Excel believes have changed, which right after opening the file is almost none.
            $ws.EnableCalculation = $false
            $ws.EnableCalculation = $true
        }
        Invoke-Excel { $ws.Calculate() } | Out-Null
        Wait-SheetReady $app $ws
        Invoke-Excel { $ws.Calculate() } | Out-Null   # settle anything that changed while data arrived
        Wait-SheetReady $app $ws
    } finally {
        try { $app.CalculationInterruptKey = $prevKey } catch {}
    }
    try {
        if ($null -ne $ws.UsedRange.Find('#NAME?', [Type]::Missing, -4163, 2)) {
            Log "  WARNING: '$($ws.Name)' contains #NAME? errors - a data add-in is probably not loaded."
        }
    } catch {}
    Log ("'{0}' done in {1:n1} min" -f $ws.Name, $sw.Elapsed.TotalMinutes)
}

# ----------------------------------- sensitivity label ---------------------------------------
# Remember the label ID / tenant ID from a workbook that already carries the wanted label.
function Read-Label($wb) {
    try {
        $l = $wb.SensitivityLabel.GetLabel()
        if (-not $l.LabelId) { return }
        if (-not $script:SiteId) { $script:SiteId = $l.SiteId }
        if (-not $script:LabelId -and $l.LabelName -like "*$SensitivityLabelName*") {
            $script:LabelId = $l.LabelId; $script:LabelName = $l.LabelName
        }
    } catch {}
}

# Label a new workbook before it is saved, so Excel does not ask for one.
function Set-Label($wb) {
    $id   = if ($SensitivityLabelId) { $SensitivityLabelId } else { $script:LabelId }
    $name = if ($script:LabelName)   { $script:LabelName }   else { $SensitivityLabelName }
    $site = if ($SensitivitySiteId)  { $SensitivitySiteId }  else { $script:SiteId }
    if (-not $id) {
        Log "  WARNING: could not find the ID of the '$SensitivityLabelName' label, so the file is saved unlabelled (Excel may ask). See `$SensitivityLabelId at the top of the script."
        return
    }
    try {
        $info = $wb.SensitivityLabel.CreateLabelInfo()
        $info.AssignmentMethod = 1                      # set on the user's behalf
        $info.LabelId   = $id
        $info.LabelName = $name
        if ($site) { $info.SiteId = $site }
        $wb.SensitivityLabel.SetLabel($info, $info)
        Log "  labelled '$name'"
    } catch {
        Log "  WARNING: could not apply the '$name' label: $($_.Exception.Message)"
    }
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
        Read-Label $wb
        Set-Label $snapWb
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
        Read-Label $src
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

        if ($live) { Read-Label $live }
        Set-Label $out
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
    if ($ShowLabel) {
        try { $app = [Runtime.InteropServices.Marshal]::GetActiveObject('Excel.Application') }
        catch { $app = New-Object -ComObject Excel.Application; $app.Visible = $true; $app.UserControl = $true }
        $f = $app.Workbooks.Open($ShowLabel, 0, $true)
        $l = $f.SensitivityLabel.GetLabel()
        Log "Label name: '$($l.LabelName)'   SensitivityLabelId: $($l.LabelId)   SensitivitySiteId: $($l.SiteId)"
        $f.Close($false)
        exit 0
    }

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
