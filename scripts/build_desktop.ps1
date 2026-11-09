$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$csc = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $csc /nologo /target:winexe /platform:x64 /optimize+ "/win32manifest:$taskRoot\scripts\DesktopHost.manifest" "/win32icon:$taskRoot\app\web\icon.ico" /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll "/reference:$taskRoot\desktop\Microsoft.Web.WebView2.Core.dll" "/reference:$taskRoot\desktop\Microsoft.Web.WebView2.WinForms.dll" "/out:$taskRoot\desktop\SCNU.Window.exe" "$taskRoot\scripts\DesktopHost.cs"
exit $LASTEXITCODE
