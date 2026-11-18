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
    # Which column of the snap holds the FRA date used to line the two days up
    # (1 = first column of the snap = column P of the Fras tab).
    [int]$CompareDateColumn = 1,
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

# Find EURCurves_Demo if it is already open in ANY Excel window. Excel registers every open
# workbook in the Windows "running object table", so this works even when GetActiveObject cannot
# see Excel (e.g. Excel has been in the foreground since it started) and for OneDrive files that
# Excel has open under their https:// address.
if (-not ('RotHelper' -as [type])) {
  try {
    Add-Type -ErrorAction Stop -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
public static class RotHelper {
    [DllImport("ole32.dll")] static extern int GetRunningObjectTable(int r, out IRunningObjectTable rot);
    [DllImport("ole32.dll")] static extern int CreateBindCtx(int r, out IBindCtx ctx);
    public static object Find(string fileName) {
        IRunningObjectTable rot; IEnumMoniker e; IBindCtx ctx;
        if (GetRunningObjectTable(0, out rot) != 0) return null;
        CreateBindCtx(0, out ctx);
        rot.EnumRunning(out e);
        IMoniker[] m = new IMoniker[1];
        while (e.Next(1, m, IntPtr.Zero) == 0) {
            string name = null;
            try { m[0].GetDisplayName(ctx, null, out name); } catch { continue; }
            if (name == null) continue;
            string n = Uri.UnescapeDataString(name).Replace('/', '\\');
            if (n.EndsWith("\\" + fileName, StringComparison.OrdinalIgnoreCase)) {
                object o; rot.GetObject(m[0], out o); return o;
            }
        }
        return null;
    }
}
'@
  } catch { }    # if this is blocked on the PC, fall back to asking Excel directly (as before)
}

function Find-OpenWorkbook {
    foreach ($ext in 'xlsm', 'xlsx', 'xlsb', 'xls') {
        if (-not ('RotHelper' -as [type])) { break }
        try {
            $wb = [RotHelper]::Find("$WorkbookName.$ext")
            if ($wb) { $null = $wb.Name; return $wb }
        } catch {}
    }
    try {                                                     # fallback: ask the active Excel
        $app = [Runtime.InteropServices.Marshal]::GetActiveObject('Excel.Application')
        return (Invoke-Excel { Find-Workbook $app } 5)
    } catch { return $null }
}

# Get the workbook. If it is already open in Excel, use that. Otherwise start Excel ourselves,
# load the installed add-ins (Excel does not load them on its own when started by a script) and
# open the workbook. Nothing needs to be open beforehand.
function Get-ExcelWorkbook {
    if ($script:CachedWb) {                                   # workbook we already found in this run
        try { $null = $script:CachedWb.Name; return $script:CachedWb } catch { $script:CachedWb = $null }
    }

    $wb = Find-OpenWorkbook
    if ($wb) { Log "Using already-open workbook $($wb.Name)"; $script:CachedWb = $wb; return $wb }
    Log "$WorkbookName is not open - starting Excel and opening it."

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

# Turn a cell value into a whole-day key (Excel date serial) so dates can be matched.
function Get-DateKey($v) {
    if ($null -eq $v) { return $null }
    if ($v -is [double] -or $v -is [int]) {
        if ($v -ge 1) { return [int][math]::Floor([double]$v) }
        return $null
    }
    if ($v -is [string] -and $v.Trim()) {
        $d = [datetime]::MinValue
        $fmts = [string[]]@('dd/MM/yyyy', 'd/M/yyyy', 'dd-MM-yyyy', 'd-M-yyyy', 'dd.MM.yyyy', 'yyyy-MM-dd')
        if ([datetime]::TryParseExact($v.Trim(), $fmts, [Globalization.CultureInfo]::InvariantCulture,
                                      [Globalization.DateTimeStyles]::None, [ref]$d)) {
            return [int][math]::Floor($d.ToOADate())
        }
    }
    return $null
}

# Build the 'Comparison' tab: column A = the later date's FRA dates (from A2 down), every other
# column = later minus earlier for the SAME FRA date. Rows are matched on the date, not on the row
# number, because the curve rolls forward each business day.
function Add-ComparisonTab($app, $out, $early, $late, [string]$earlyName, [string]$lateName) {
    $shape = $early.Range($SnapRange)
    $rows = $shape.Rows.Count; $cols = $shape.Columns.Count
    $dc = $CompareDateColumn
    if ($dc -lt 1 -or $dc -gt $cols) { throw "CompareDateColumn must be between 1 and $cols." }

    Log "Building 'Comparison' tab ($lateName minus $earlyName, matched on date) ..."
    $E = $early.Range('A1').Resize($rows, $cols).Value2
    $L = $late.Range('A1').Resize($rows, $cols).Value2

    # Row 1 of the snap is a header if its date column does not hold a date.
    $hasHeader = ($null -eq (Get-DateKey $L[1, $dc]))
    $first = if ($hasHeader) { 2 } else { 1 }

    # Earlier day: FRA date -> row
    $idx = @{}
    for ($r = $first; $r -le $rows; $r++) {
        $k = Get-DateKey $E[$r, $dc]
        if ($null -ne $k -and -not $idx.ContainsKey($k)) { $idx[$k] = $r }
    }
    if ($idx.Count -eq 0) {
        throw "No dates found in column $dc of '$earlyName'. Set CompareDateColumn at the top of the script to the snap column that holds the FRA date."
    }

    # Output columns: the date first, then every other snap column in order.
    $srcCols = @($dc) + @(1..$cols | Where-Object { $_ -ne $dc })

    # Columns that hold dates (e.g. start / end dates) are shown as the later day's value, not diffed.
    $isDate = @{}
    foreach ($c in $srcCols) {
        $fmt = [string]$late.Cells.Item($first, $c).NumberFormat
        $isDate[$c] = ($c -ne $dc) -and ($fmt -match '[dmy]') -and ($fmt -notmatch '^General$')
    }

    $outRows = $rows - $first + 2
    $A = New-Object 'object[,]' $outRows, $cols
    for ($j = 0; $j -lt $cols; $j++) {
        $c = $srcCols[$j]
        $h = if ($hasHeader -and $null -ne $L[1, $c]) { [string]$L[1, $c] } else { "Column $c" }
        if ($j -eq 0) { $h = "Date ($lateName)" }
        elseif (-not $isDate[$c]) { $h = "$h change" }
        $A[0, $j] = $h
    }

    $matched = 0; $unmatched = @()
    for ($r = $first; $r -le $rows; $r++) {
        $k = Get-DateKey $L[$r, $dc]
        if ($null -eq $k) { continue }
        $i = $r - $first + 1
        $A[$i, 0] = [double]$k
        $er = $idx[$k]
        if ($er) { $matched++ } else { $unmatched += [datetime]::FromOADate($k).ToString('dd-MM-yyyy') }
        for ($j = 1; $j -lt $cols; $j++) {
            $c = $srcCols[$j]
            $lv = $L[$r, $c]
            if ($isDate[$c]) { $A[$i, $j] = $lv; continue }
            if ($er) {
                $ev = $E[$er, $c]
                if (($lv -is [double]) -and ($ev -is [double])) { $A[$i, $j] = $lv - $ev }
            }
        }
    }

    $ws = $out.Worksheets.Add([Type]::Missing, $out.Worksheets.Item($out.Worksheets.Count))
    $ws.Name = 'Comparison'
    $ws.Range('A1').Resize($outRows, $cols).Value2 = $A
    $ws.Columns.Item(1).NumberFormat = 'dd/mm/yyyy'
    for ($j = 1; $j -lt $cols; $j++) {
        try { $ws.Columns.Item($j + 1).NumberFormat = $late.Cells.Item($first, $srcCols[$j]).NumberFormat } catch {}
    }
    $ws.Rows.Item(1).Font.Bold = $true
    try { $ws.Columns.AutoFit() | Out-Null } catch {}
    try { $ws.Activate(); $app.ActiveWindow.SplitRow = 1; $app.ActiveWindow.FreezePanes = $true } catch {}

    Log "  $matched dates matched."
    if ($unmatched.Count) {
        $show = ($unmatched | Select-Object -First 5) -join ', '
        Log "  $($unmatched.Count) dates in $lateName are not in $earlyName, left blank (e.g. $show)."
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
        # Live rates come from the copy of EURCurves_Demo you already have open (Initialization and
        # Grp already calculated there), so only the Fras tab is refreshed.
        $live = Find-OpenWorkbook
        if (-not $live) {
            throw "$WorkbookName is not open. Open it in Excel (with Initialization and Grp refreshed), then run the comparison again."
        }
        Log "Using open workbook $($live.Name) - refreshing '$FrasSheet' only"
        $app = $live.Application
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

        Add-ComparisonTab $app $out $tab1 $tab2 $tab1.Name $tab2.Name

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
