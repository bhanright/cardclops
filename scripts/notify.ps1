# Shows a Windows notification for the gallery's price alerts.
# Runs under Windows PowerShell 5.1 (powershell.exe), which can load the WinRT
# toast API directly; PowerShell 7 cannot. Clicking the notification opens $Url.
param([string]$Title, [string]$Body, [string]$Url = "http://localhost:8765/#/alerts")

[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null

$escape = { param($text) [System.Security.SecurityElement]::Escape($text) }
$lines = ($Body -split "`n" | ForEach-Object { "<text>$(& $escape $_)</text>" }) | Select-Object -First 2
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml(@"
<toast activationType="protocol" launch="$(& $escape $Url)">
  <visual><binding template="ToastGeneric">
    <text>$(& $escape $Title)</text>
    $($lines -join "`n    ")
  </binding></visual>
</toast>
"@)

# Notifications need an app identity; Windows PowerShell's own is always registered.
$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show(
    [Windows.UI.Notifications.ToastNotification]::new($xml))
