param(
    [Parameter(Mandatory = $true)]
    [string] $ProjectFile
)

$ErrorActionPreference = 'Stop'
$projectPath = [System.IO.Path]::GetFullPath($ProjectFile)
$packageDir = [System.IO.Path]::GetDirectoryName($projectPath)
if (-not [System.IO.File]::Exists($projectPath)) {
    throw "Project not found: $projectPath"
}

$inputBytes = [System.IO.File]::ReadAllBytes($projectPath)
$inputStream = [System.IO.MemoryStream]::new($inputBytes)
$decompressor = [System.IO.Compression.GZipStream]::new(
    $inputStream, [System.IO.Compression.CompressionMode]::Decompress)
$document = [System.Xml.XmlDocument]::new()
$document.PreserveWhitespace = $true
try {
    $document.Load($decompressor)
} finally {
    $decompressor.Dispose()
    $inputStream.Dispose()
}
if ($document.DocumentElement.Name -ne 'PremiereData') {
    throw 'Unsupported Premiere project format.'
}

$mediaNodes = $document.SelectNodes('/PremiereData/Media')
if ($mediaNodes.Count -eq 0) {
    throw 'No media records found in project.'
}
$packagePrefix = $packageDir.TrimEnd('\') + '\'
foreach ($media in $mediaNodes) {
    $relativeNode = $media.SelectSingleNode('RelativePath')
    if ($null -eq $relativeNode) {
        throw 'Media record has no RelativePath.'
    }
    $relative = $relativeNode.InnerText.Replace('/', '\')
    if (-not $relative.StartsWith('.\media\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Media path is outside packaged media: $relative"
    }
    $fullPath = [System.IO.Path]::GetFullPath(
        [System.IO.Path]::Combine($packageDir, $relative))
    if (-not $fullPath.StartsWith($packagePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Media path escapes package: $relative"
    }
    if (-not [System.IO.File]::Exists($fullPath)) {
        throw "Packaged media not found: $fullPath"
    }
    foreach ($field in @('FilePath', 'ActualMediaFilePath')) {
        $node = $media.SelectSingleNode($field)
        if ($null -eq $node) {
            throw "Media record missing $field : $relative"
        }
        $node.InnerText = $fullPath
    }
}

$lastKnown = $document.SelectSingleNode(
    '//project.settings.lastknowngoodprojectpath')
if ($null -ne $lastKnown) {
    $lastKnown.InnerText = $projectPath
}
$xmlStream = [System.IO.MemoryStream]::new()
$document.Save($xmlStream)
$xmlBytes = $xmlStream.ToArray()
$xmlStream.Dispose()
$outputStream = [System.IO.MemoryStream]::new()
$compressor = [System.IO.Compression.GZipStream]::new(
    $outputStream, [System.IO.Compression.CompressionLevel]::Optimal, $true)
try {
    $compressor.Write($xmlBytes, 0, $xmlBytes.Length)
} finally {
    $compressor.Dispose()
}
$outputBytes = $outputStream.ToArray()
$outputStream.Dispose()

$temporary = $projectPath + '.relink-' + [System.Guid]::NewGuid().ToString('N') + '.tmp'
$backup = $projectPath + '.before-relink-' +
    (Get-Date -Format 'yyyyMMdd-HHmmss') + '.bak'
try {
    [System.IO.File]::WriteAllBytes($temporary, $outputBytes)
    [System.IO.File]::Replace($temporary, $projectPath, $backup)
} finally {
    if ([System.IO.File]::Exists($temporary)) {
        [System.IO.File]::Delete($temporary)
    }
}
Write-Host "Updated $($mediaNodes.Count) media paths: $projectPath"
Write-Host "Original project backup: $backup"
