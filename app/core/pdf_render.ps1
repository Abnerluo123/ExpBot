param(
    [Parameter(Mandatory = $true)][string]$InPath,
    [Parameter(Mandatory = $true)][string]$OutPath,
    [double]$Scale = 2.0,
    [int]$PageIndex = 0
)
# 用 Windows 自带的 Windows.Data.Pdf 把 PDF 第一页渲染为 PNG，无需任何第三方库。
$ErrorActionPreference = 'Stop'
$statusPath = "$OutPath.status.txt"
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $ext = [System.WindowsRuntimeSystemExtensions].GetMethods()
    $asTaskOp = ($ext | Where-Object {
            $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
            $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
        })[0]
    $asTaskAction = ($ext | Where-Object {
            $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
            $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction'
        })[0]
    function Await($op, [Type]$resultType) {
        $task = $asTaskOp.MakeGenericMethod($resultType).Invoke($null, @($op))
        $task.Wait(-1) | Out-Null
        return $task.Result
    }
    function AwaitAction($action) {
        $task = $asTaskAction.Invoke($null, @($action))
        $task.Wait(-1) | Out-Null
    }

    [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Pdf.PdfDocument, Windows.Data.Pdf, ContentType = WindowsRuntime] | Out-Null
    [Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime] | Out-Null

    # Use the ordinary desktop file stream, not WinRT's app-folder StorageFile policy.
    $fileStream = [System.IO.File]::OpenRead($InPath)
    $randomStream = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($fileStream)
    $doc = Await ([Windows.Data.Pdf.PdfDocument]::LoadFromStreamAsync($randomStream)) ([Windows.Data.Pdf.PdfDocument])
    if ($PageIndex -lt 0 -or $PageIndex -ge $doc.PageCount) { throw 'Requested PDF page does not exist.' }
    $page = $doc.GetPage($PageIndex)
    $targetWidth = [uint64][math]::Ceiling($page.Size.Width * $Scale)
    $targetHeight = [uint64][math]::Ceiling($page.Size.Height * $Scale)
    if ($targetWidth -lt 1 -or $targetHeight -lt 1 -or $targetWidth -gt 9000 -or $targetHeight -gt 9000 -or ($targetWidth * $targetHeight) -gt 45000000) {
        throw 'The rendered PDF page exceeds the safe pixel limit.'
    }
    $opts = New-Object Windows.Data.Pdf.PdfPageRenderOptions
    $opts.DestinationWidth = [uint32]$targetWidth
    $opts.DestinationHeight = [uint32]$targetHeight
    $stream = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
    AwaitAction ($page.RenderToStreamAsync($stream, $opts))

    $input = $stream.GetInputStreamAt(0)
    $net = [System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($input)
    $fs = [System.IO.File]::Create($OutPath)
    try { $net.CopyTo($fs) } finally { $fs.Close() }
    $info = "OK pages=$($doc.PageCount) width=$($page.Size.Width) height=$($page.Size.Height)"
    [System.IO.File]::WriteAllText($statusPath, $info, [System.Text.Encoding]::UTF8)
}
catch {
    $err = $_.Exception
    $messages = @()
    while ($null -ne $err) {
        $messages += ($err.GetType().FullName + ': ' + $err.Message + ' HRESULT=' + $err.HResult)
        $err = $err.InnerException
    }
    $messages += $_.ScriptStackTrace
    [System.IO.File]::WriteAllText($statusPath, ('ERROR ' + ($messages -join "`n")), [System.Text.Encoding]::UTF8)
    exit 1
}
