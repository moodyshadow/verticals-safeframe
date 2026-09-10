# Runs shortly after the DailyOverclocked_OpportunityScan task finishes
# (that task scans+drafts+produces but deliberately stops before upload,
# per run_full_pipeline.py). This closes the gap YouTube's own analytics
# flagged as "inconsistent publishing": a video gets produced every day at
# 7am, but nothing forced it to actually get reviewed and uploaded, so this
# makes today's result impossible to miss instead of relying on memory.

$statusPath = "G:\AI\YoutubeShortsPipeline\reports\pipeline_status.json"
$logPath = "G:\AI\YoutubeShortsPipeline\reports\review_reminder_log.txt"

function Write-Log($msg) {
    "$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss')) $msg" | Out-File -Append -FilePath $logPath -Encoding utf8
}

if (-not (Test-Path $statusPath)) {
    Write-Log "No pipeline_status.json found - scan may not have run yet."
    exit 0
}

$status = Get-Content $statusPath -Raw | ConvertFrom-Json
$statusAge = (Get-Date) - (Get-Item $statusPath).LastWriteTime
if ($statusAge.TotalHours -gt 12) {
    Write-Log "pipeline_status.json is $([math]::Round($statusAge.TotalHours,1))h old - skipping, this isn't today's run."
    exit 0
}

if ($status.ok -eq $true) {
    $title = $status.title
    $videoPath = $status.video_path
    $warning = ""
    if ($status.degraded) {
        $warning = "`n`nWARNING: b-roll fell back to plain gradients - check visuals before uploading."
    }

    Write-Log "Video ready: $title ($videoPath)"

    if (Test-Path $videoPath) {
        Start-Process $videoPath
    }

    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        "Today's video is ready for review:`n`n$title$warning`n`nUpload when ready:`npython -m verticals upload --draft <draft_path>",
        "Daily Overclocked - review + upload today",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Information
    ) | Out-Null
} else {
    Write-Log "Pipeline failed at stage $($status.stage): $($status.error)"
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        "Today's video pipeline FAILED at stage: $($status.stage)`n`n$($status.error)",
        "Daily Overclocked - pipeline failed",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
}
