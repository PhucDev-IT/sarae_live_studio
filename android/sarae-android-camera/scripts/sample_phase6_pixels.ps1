$ErrorActionPreference='Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
Add-Type -AssemblyName System.Drawing
$dir=Join-Path (Get-Location) 'test-results/phase6/actual-preview'
$results=@()
foreach($name in @('A-before','B-after','A-after')) {
    $bitmap=[System.Drawing.Bitmap]::new((Join-Path $dir "$name.png"))
    try {
        $red=0L; $green=0L; $blue=0L; $count=0
        for($y=600;$y -lt 1000;$y+=10){
            for($x=400;$x -lt 680;$x+=10){
                $pixel=$bitmap.GetPixel($x,$y)
                $red+=$pixel.R; $green+=$pixel.G; $blue+=$pixel.B; $count++
            }
        }
        $results+=@{image="$name.png";width=$bitmap.Width;height=$bitmap.Height;
            roi=@(400,600,680,1000);samples=$count;rgb=@(($red/$count),($green/$count),($blue/$count))}
    } finally {$bitmap.Dispose()}
}
$results | ConvertTo-Json -Depth 5 | Set-Content "$dir/pixel-evidence.json"
$results | ConvertTo-Json -Depth 5
