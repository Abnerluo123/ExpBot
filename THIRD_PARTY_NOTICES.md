# Third-party notices

## Python
The portable edition includes CPython 3.12. The PSF license is included in `licenses/Python-LICENSE.txt`.

## Pillow
The portable edition includes Pillow 12.3.0, distributed under the Pillow license. See `licenses/Pillow/LICENSE`.

## pypdf
The application includes pypdf 5.9.0 (pure Python, BSD-3-Clause) under `app/_vendor/pypdf`. Its license is included at `app/_vendor/pypdf-5.9.0.dist-info/licenses/LICENSE`; its wheel source and verified SHA256 are recorded in `app/_vendor/pypdf-source.json`.

## Certificate trust store
The portable edition includes the Mozilla CA bundle distributed by certifi. Its license is included in `licenses/certifi-LICENSE.txt`.

## Microsoft WebView2
The desktop host includes Microsoft.Web.WebView2 SDK 1.0.3485.44 WinForms/Core assemblies and the x64 WebView2Loader. The official NuGet package source and SHA256 are recorded in `desktop/webview2-sdk.json`; redistribution license is included at `desktop/WebView2-LICENSE.txt`. No SDK install scripts are used. The Edge WebView2 Runtime itself is a system-provided Windows component and is not bundled.

Windows PowerShell, Windows.Data.Pdf and Microsoft Edge are system-provided Windows components and are not bundled.
