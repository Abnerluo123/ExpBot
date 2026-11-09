"""Vendor official Microsoft WebView2 host assemblies; no installer or build scripts."""
import hashlib,io,json,sys,urllib.request,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'desktop';OUT.mkdir(exist_ok=True)
# Pin official stable SDK. WebView2 runtime remains the installed Windows component.
VERSION='1.0.3485.44'
url=f'https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/{VERSION}/microsoft.web.webview2.{VERSION}.nupkg'
with urllib.request.urlopen(url,timeout=30) as response:payload=response.read(40*1024*1024)
with zipfile.ZipFile(io.BytesIO(payload)) as package:
    for source,target in {'lib/net462/Microsoft.Web.WebView2.Core.dll':'Microsoft.Web.WebView2.Core.dll','lib/net462/Microsoft.Web.WebView2.WinForms.dll':'Microsoft.Web.WebView2.WinForms.dll','runtimes/win-x64/native/WebView2Loader.dll':'WebView2Loader.dll','LICENSE.txt':'WebView2-LICENSE.txt'}.items():
        (OUT/target).write_bytes(package.read(source))
metadata={'package':'Microsoft.Web.WebView2','version':VERSION,'source':url,'sha256':hashlib.sha256(payload).hexdigest()}
(OUT/'webview2-sdk.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8');print(json.dumps(metadata))
