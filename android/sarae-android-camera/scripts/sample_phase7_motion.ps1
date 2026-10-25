$ErrorActionPreference='Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
Add-Type -AssemblyName System.Drawing
$result=@()
foreach($item in @(@{run='trace-run';source='B'},@{run='trace-complete';source='A'})){
    $directory=Join-Path (Get-Location) "test-results/phase7/$($item.run)"
    $a=[System.Drawing.Bitmap]::new((Join-Path $directory "$($item.source)-after-trace-1.png"))
    $b=[System.Drawing.Bitmap]::new((Join-Path $directory "$($item.source)-after-trace-2.png"))
    try {
        $difference=0L; $changed=0; $samples=0; $blackA=0; $blackB=0
        for($y=500;$y -lt 1200;$y+=10){for($x=300;$x -lt 780;$x+=10){
            $p=$a.GetPixel($x,$y); $q=$b.GetPixel($x,$y)
            $delta=[Math]::Abs([int]$p.R-$q.R)+[Math]::Abs([int]$p.G-$q.G)+[Math]::Abs([int]$p.B-$q.B)
            $difference+=$delta; $samples++
            if($delta -gt 30){$changed++}
            if($p.R -lt 10 -and $p.G -lt 10 -and $p.B -lt 10){$blackA++}
            if($q.R -lt 10 -and $q.G -lt 10 -and $q.B -lt 10){$blackB++}
        }}
        $result+=@{run=$item.run;source=$item.source;roi=@(300,500,780,1200);samples=$samples;
            meanAbsoluteChannelDifference=$difference/($samples*3);changedFraction=$changed/$samples;
            firstBlackFraction=$blackA/$samples;secondBlackFraction=$blackB/$samples}
    }finally{$a.Dispose();$b.Dispose()}
}
$result | ConvertTo-Json -Depth 5 | Set-Content test-results/phase7/motion-evidence.json
$result | ConvertTo-Json -Depth 5
