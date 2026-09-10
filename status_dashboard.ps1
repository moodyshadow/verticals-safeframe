# Live status dashboard for the Daily Overclocked pipeline — a persistent
# terminal window showing local service health (fast refresh) and
# YouTube/TikTok/Instagram follower/view counts (slow refresh, to avoid
# hammering those APIs). Meant to be left open, not run once.
#
# Local ports checked mirror what the pipeline actually depends on:
# main Ollama (11434), marketing-helper Ollama (11435, CPU-only),
# the original A1111 webui (7860, used by gaming's real-SD b-roll),
# Forge (7861), and ComfyUI (8188) if it's ever left running.

$venvPython = "G:\AI\YoutubeShortsPipeline\venv\Scripts\python.exe"
$statsScript = "G:\AI\YoutubeShortsPipeline\social_stats.py"
$socialRefreshEverySeconds = 60
$localRefreshSeconds = 5

$services = @(
    @{ Name = "Ollama (main, 11434)";        Url = "http://127.0.0.1:11434/api/tags" }
    @{ Name = "Ollama (marketing, 11435)";   Url = "http://127.0.0.1:11435/api/tags" }
    @{ Name = "SD webui A1111 (7860)";       Url = "http://127.0.0.1:7860/sdapi/v1/sd-models" }
    @{ Name = "Forge (7861)";                Url = "http://127.0.0.1:7861/sdapi/v1/sd-models" }
    @{ Name = "ComfyUI (8188)";              Url = "http://127.0.0.1:8188/system_stats" }
)

function Test-Service($url) {
    try {
        $r = Invoke-WebRequest -Uri $url -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

$lastSocial = $null
$lastSocialFetch = [DateTime]::MinValue

while ($true) {
    Clear-Host
    $now = Get-Date
    Write-Host "=== Daily Overclocked — Live Status ===" -ForegroundColor Cyan
    Write-Host "  $($now.ToString('yyyy-MM-dd HH:mm:ss'))`n"

    Write-Host "-- Local services --" -ForegroundColor Yellow
    foreach ($svc in $services) {
        $up = Test-Service $svc.Url
        if ($up) {
            Write-Host ("  [UP]   {0}" -f $svc.Name) -ForegroundColor Green
        } else {
            Write-Host ("  [DOWN] {0}" -f $svc.Name) -ForegroundColor Red
        }
    }

    if (($now - $lastSocialFetch).TotalSeconds -ge $socialRefreshEverySeconds -or $null -eq $lastSocial) {
        try {
            $raw = & $venvPython $statsScript 2>$null | Select-Object -Last 1
            $lastSocial = $raw | ConvertFrom-Json
        } catch {
            $lastSocial = $null
        }
        $lastSocialFetch = $now
    }

    Write-Host "`n-- Social stats (refreshes every $socialRefreshEverySeconds s) --" -ForegroundColor Yellow
    if ($null -eq $lastSocial) {
        Write-Host "  (fetch failed — check social_stats.py manually)" -ForegroundColor Red
    } else {
        $yt = $lastSocial.youtube
        if ($yt.error) {
            Write-Host ("  YouTube:   error — {0}" -f $yt.error) -ForegroundColor Red
        } else {
            Write-Host ("  YouTube:   {0} subscribers | {1} views | {2} videos" -f $yt.subscribers, $yt.views, $yt.videos) -ForegroundColor White
        }

        $tt = $lastSocial.tiktok
        if ($tt.error) {
            Write-Host ("  TikTok:    unavailable — {0}" -f $tt.error) -ForegroundColor DarkGray
        } else {
            Write-Host ("  TikTok:    {0} followers | {1} likes | {2} videos" -f $tt.followers, $tt.likes, $tt.videos) -ForegroundColor White
        }

        $ig = $lastSocial.instagram
        if ($ig.error) {
            Write-Host ("  Instagram: error — {0}" -f $ig.error) -ForegroundColor Red
        } else {
            Write-Host ("  Instagram: {0} followers | {1} posts" -f $ig.followers, $ig.posts) -ForegroundColor White
        }
    }

    Write-Host "`n(Ctrl+C to close)"
    Start-Sleep -Seconds $localRefreshSeconds
}
