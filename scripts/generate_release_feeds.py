#!/usr/bin/env python3

import json
import os
import re
import shutil
import urllib.request
import zipfile
import plistlib
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict
import yaml

@dataclass
class AppInfo:
    bundle_id: str = ""
    app_name: str = ""
    version: str = ""
    build: str = ""
    min_ios: str = "14.0"
    device_families: list = field(default_factory=lambda: [1, 2])
    tweaks: list = field(default_factory=list)
    entitlements: dict = field(default_factory=dict)
    privacy: dict = field(default_factory=dict)
    file_name: str = ""
    file_size: int = 0
    file_date: str = ""
    download_url: str = ""
    release_notes: str = ""

def resolve_url(val: str, repo_name: str, branch: str) -> str:
    if not val:
        return ""
    if val.startswith("http://") or val.startswith("https://") or val.startswith("mailto:"):
        return val
    return f"https://raw.githubusercontent.com/{repo_name}/{branch}/{val.lstrip('/')}"

def load_config(repo_name: str, owner: str, repo: str, branch: str) -> dict:
    config_path = Path('.github/config.yml')
    defaults = {
        'source_name': f'{repo}',
        'source_id': 'auto',
        'source_subtitle': 'Automated iOS Release Repository',
        'source_description': f'Latest iOS application releases and enhancements from {owner}.',
        'developer_name': 'auto',
        'tint_color': '#FF3366',
        'icon_url': 'Assets/repo_icon.png',
        'header_url': 'Assets/repo_header.png',
        'website': 'auto',
        'screenshots': [],
        'max_versions': 15,
        'min_ios_version_fallback': '14.0',
        'news_url': 'auto',
    }
    
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            custom = yaml.safe_load(f) or {}
        for k, v in custom.items():
            if v is not None: defaults[k] = v

    if defaults['source_id'] == 'auto' or not defaults['source_id']:
        defaults['source_id'] = f"com.{owner.lower()}/{repo.lower()}".replace('/', '.')
    if defaults['developer_name'] == 'auto' or not defaults['developer_name']:
        defaults['developer_name'] = owner
    if defaults['website'] == 'auto' or not defaults['website']:
        defaults['website'] = f"https://github.com/{repo_name}"
    if defaults['news_url'] == 'auto' or not defaults['news_url']:
        defaults['news_url'] = f"https://github.com/{repo_name}/releases"

    defaults['icon_url'] = resolve_url(str(defaults.get('icon_url', '')), repo_name, branch)
    defaults['header_url'] = resolve_url(str(defaults.get('header_url', '')), repo_name, branch)
    
    if isinstance(defaults.get('screenshots'), list):
        defaults['screenshots'] = [resolve_url(str(s), repo_name, branch) for s in defaults['screenshots'] if s]
        
    print(f"🔧 Active Repository Configuration: Owner={owner}, Repo={repo}, Branch={branch}")
    return defaults

def load_app_md_description() -> Optional[str]:
    app_md_path = Path("app.md")
    if app_md_path.exists():
        try:
            with open(app_md_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                print(f"Loaded custom localizedDescription from app.md ({len(content)} characters)")
                return content
        except Exception as e:
            print(f"⚠️ Failed reading app.md: {e}")
    return None

def download_file(url: str, dest_path: Path) -> bool:
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp, open(dest_path, 'wb') as f:
            shutil.copyfileobj(resp, f)
        return True
    except Exception as e:
        print(f"❌ HTTP download failed for {url}: {e}")
        return False

def detect_tweaks(zf: zipfile.ZipFile, app_folder: str) -> list:
    system_libs = {'libSystem', 'libobjc', 'libc++', 'libswift', 'libz', 'libsqlite', 'libdispatch'}
    tweaks = []
    for name in zf.namelist():
        if name.startswith(f"{app_folder}Frameworks/") and name.endswith('.dylib'):
            dylib_name = Path(name).stem
            if not any(dylib_name.startswith(s) for s in system_libs):
                tweaks.append(dylib_name)
    return tweaks

def extract_privacy_and_entitlements(plist: dict) -> dict:
    privacy_keys = [k for k in plist.keys() if k.startswith("NS") and "UsageDescription" in k]
    return {k: plist[k] for k in privacy_keys}

def process_ipa_asset(asset_path: Path, download_url: str, upload_date: str, file_size: int, release_notes: str) -> Optional[AppInfo]:
    try:
        with zipfile.ZipFile(asset_path, 'r') as zf:
            for name in zf.namelist():
                if name.startswith('Payload/') and name.endswith('.app/Info.plist'):
                    with zf.open(name) as f:
                        plist = plistlib.load(f)
                    
                    app_folder = name.rsplit('/', 2)[0] + '/'
                    tweaks = detect_tweaks(zf, app_folder)
                    privacy = extract_privacy_and_entitlements(plist)
                    
                    return AppInfo(
                        bundle_id=plist.get('CFBundleIdentifier', 'com.apex.app'),
                        app_name=plist.get('CFBundleDisplayName', plist.get('CFBundleName', asset_path.stem)),
                        version=plist.get('CFBundleShortVersionString', '1.0.0'),
                        build=str(plist.get('CFBundleVersion', '1')),
                        min_ios=str(plist.get('MinimumOSVersion', '14.0')),
                        device_families=plist.get('UIDeviceFamily', [1, 2]),
                        tweaks=tweaks,
                        privacy=privacy,
                        file_name=asset_path.name,
                        file_size=file_size,
                        file_date=upload_date,
                        download_url=download_url,
                        release_notes=release_notes
                    )
    except Exception as e:
        print(f"⚠️ Error reading IPA structure from {asset_path.name}: {e}")
    return None

def generate_store_json(apps: List[AppInfo], config: dict, app_md_desc: Optional[str], developer_fallback: str) -> dict:
    apps_by_bundle = defaultdict(list)
    for app in apps:
        apps_by_bundle[app.bundle_id].append(app)
    
    tint_color = config['tint_color'] if config['tint_color'].startswith('#') else f"#{config['tint_color']}"
    app_entries = []
    
    for bundle_id, bundle_apps in apps_by_bundle.items():
        bundle_apps.sort(key=lambda x: x.file_date, reverse=True)
        
        unique_apps = []
        seen_versions = set()
        for app in bundle_apps:
            if app.version not in seen_versions:
                seen_versions.add(app.version)
                unique_apps.append(app)
                
        bundle_apps = unique_apps[:int(config['max_versions'])]
        primary = bundle_apps[0]
        
        versions = []
        for app in bundle_apps:
            base_desc = f"Release v{app.version}" + (f" | Injected: {', '.join(app.tweaks)}" if app.tweaks else "")
            full_desc = f"{base_desc}\n\n{app.release_notes}".strip() if app.release_notes else base_desc
            versions.append({
                "version": app.version,
                "date": app.file_date,
                "localizedDescription": full_desc,
                "downloadURL": app.download_url,
                "size": app.file_size
            })
            
        subtitle = f"Enhanced with {', '.join(primary.tweaks[:2])}" if primary.tweaks else f"{primary.app_name} for iOS"
        localized_desc = app_md_desc if app_md_desc is not None else f"{primary.app_name} package provided by {config['source_name']}."
        
        app_record = {
            "name": primary.app_name,
            "bundleIdentifier": bundle_id,
            "developerName": config['developer_name'] or developer_fallback,
            "subtitle": subtitle,
            "version": primary.version,
            "versionDate": primary.file_date,
            "versionDescription": versions[0]["localizedDescription"],
            "downloadURL": primary.download_url,
            "localizedDescription": localized_desc,
            "iconURL": config['icon_url'],
            "tintColor": tint_color,
            "size": primary.file_size,
            "appPermissions": {"privacy": primary.privacy},
            "versions": versions
        }
        
        if config.get('screenshots') and isinstance(config['screenshots'], list) and len(config['screenshots']) > 0:
            app_record['screenshots'] = config['screenshots']
            
        app_entries.append(app_record)
        
    return {
        "name": config['source_name'],
        "identifier": config['source_id'],
        "subtitle": config['source_subtitle'],
        "description": config['source_description'],
        "iconURL": config['icon_url'],
        "headerURL": config['header_url'],
        "website": config['website'],
        "tintColor": tint_color,
        "apps": app_entries,
        "news": []
    }

def generate_esign_json(apps: List[AppInfo], config: dict, raw_feed_url: str, app_md_desc: Optional[str], developer_fallback: str) -> dict:
    esign_apps = []
    for app in apps:
        base_desc = f"APEX Build v{app.version}" + (f"\nTweaks: {', '.join(app.tweaks)}" if app.tweaks else "")
        localized_desc = app_md_desc if app_md_desc is not None else base_desc
        
        app_data = {
            "name": f"{app.app_name} {app.version}",
            "bundleIdentifier": app.bundle_id,
            "developerName": config['developer_name'] or developer_fallback,
            "version": app.version,
            "versionDate": app.file_date,
            "downloadURL": app.download_url,
            "localizedDescription": localized_desc,
            "iconURL": config['icon_url'],
            "size": app.file_size,
        }
        if config.get('screenshots') and isinstance(config['screenshots'], list) and len(config['screenshots']) > 0:
            app_data['screenshotURLs'] = config['screenshots']
        esign_apps.append(app_data)
        
    return {
        "name": config['source_name'],
        "identifier": config['source_id'],
        "sourceURL": raw_feed_url,
        "author": config['developer_name'] or developer_fallback,
        "apps": esign_apps
    }

def generate_scarlet_json(apps: List[AppInfo], config: dict, app_md_desc: Optional[str], developer_fallback: str) -> dict:
    tint = config['tint_color'].lstrip('#')
    try:
        rgb = {"red": int(tint[0:2], 16)/255.0, "green": int(tint[2:4], 16)/255.0, "blue": int(tint[4:6], 16)/255.0}
    except:
        rgb = {"red": 1.0, "green": 0.2, "blue": 0.4}
        
    seen = {}
    for app in apps:
        if app.bundle_id not in seen:
            seen[app.bundle_id] = app
    scarlet_apps = []
    for app in seen.values():
        localized_desc = app_md_desc if app_md_desc is not None else f"{app.app_name} for iOS"
        app_data = {
            "name": app.app_name,
            "bundleIdentifier": app.bundle_id,
            "developerName": config['developer_name'] or developer_fallback,
            "localizedDescription": localized_desc,
            "version": app.version,
            "versionDate": app.file_date,
            "size": app.file_size,
            "iconURL": config['icon_url'],
            "downloadURL": app.download_url,
            "minOSVersion": app.min_ios,
            "supportedPlatforms": ["iOS"],
            "deviceFamilies": app.device_families
        }
        if config.get('screenshots') and isinstance(config['screenshots'], list) and len(config['screenshots']) > 0:
            app_data['screenshots'] = config['screenshots']
        scarlet_apps.append(app_data)

    return {
        "name": config['source_name'],
        "identifier": config['source_id'],
        "subtitle": config['source_subtitle'],
        "description": config['source_description'],
        "version": "1.0.0",
        "accentColor": {k: round(v, 2) for k, v in rgb.items()},
        "iconURL": config['icon_url'],
        "apps": scarlet_apps
    }

def main():
    repo_name = os.environ.get('GITHUB_REPOSITORY', 'lowiqentity/APEX')
    branch = os.environ.get('GITHUB_REF_NAME', 'main')
    token = os.environ.get('GITHUB_TOKEN', '')
    
    parts = repo_name.split('/')
    owner = parts[0] if len(parts) > 0 else 'APEX'
    repo = parts[1] if len(parts) > 1 else 'APEX'

    config = load_config(repo_name, owner, repo, branch)
    app_md_desc = load_app_md_description()
    
    print(f"Fetching GitHub Releases for {repo_name}...")
    api_url = f"https://api.github.com/repos/{repo_name}/releases"
    headers = {"User-Agent": "APEX-Universal-Feed-Bot"}
    if token:
        headers["Authorization"] = f"token {token}"
        
    req = urllib.request.Request(api_url, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            releases = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        print(f"❌ Failed to fetch GitHub releases from API: {e}")
        return

    scratch_dir = Path("/tmp/apex_ipas")
    if scratch_dir.exists(): shutil.rmtree(scratch_dir)
    scratch_dir.mkdir(parents=True, exist_ok=True)

    apps = []
    for release in releases:
        rel_date = release.get('published_at', '')[:10] or release.get('created_at', '')[:10] or datetime.utcnow().strftime('%Y-%m-%d')
        rel_notes = release.get('body', '').strip() if release.get('body') else ""
        
        ipa_targets = []
        
        if rel_notes:
            embedded_urls = re.findall(r'(https?://[^\s\)\"\'<]+(?:\.ipa|\.pkg)[^\s\)\"\'<]*)', rel_notes, flags=re.IGNORECASE)
            for idx, e_url in enumerate(embedded_urls):
                clean_url = e_url.rstrip('.,;:')
                fname = clean_url.split('/')[-1].split('?')[0] or f"{release.get('tag_name', 'build')}_{idx}.ipa"
                if not fname.lower().endswith('.ipa'): fname += ".ipa"
                ipa_targets.append((fname, clean_url, 0))
                print(f"Found embedded external download URL in release notes: {clean_url}")
                
        for asset in release.get('assets', []):
            if asset['name'].lower().endswith('.ipa'):
                ipa_targets.append((asset['name'], asset['browser_download_url'], asset['size']))

        if not ipa_targets:
            print(f"No IPA links or assets discovered in release {release.get('tag_name', 'unknown')}")

        for fname, dl_url, known_size in ipa_targets:
            print(f"Downloading release target: {fname} from {dl_url}...")
            local_path = scratch_dir / fname
            try:
                if download_file(dl_url, local_path):
                    actual_size = local_path.stat().st_size if local_path.exists() else known_size
                    info = process_ipa_asset(
                        asset_path=local_path,
                        download_url=dl_url,
                        upload_date=rel_date,
                        file_size=actual_size,
                        release_notes=rel_notes
                    )
                    if info:
                        apps.append(info)
                        print(f"  ✔ Analyzed {info.app_name} (v{info.version}) - Direct Link: {info.download_url} ({actual_size} bytes)")
            except Exception as dl_err:
                print(f"⚠️ Failed processing asset {fname} from {dl_url}: {dl_err}")
            finally:
                if local_path.exists(): local_path.unlink()

    out_dir = Path("JSON")
    out_dir.mkdir(exist_ok=True)
    raw_esign_url = f"https://raw.githubusercontent.com/{repo_name}/{branch}/JSON/esign.json"

    store_feed = generate_store_json(apps, config, app_md_desc, developer_fallback=owner)
    with open(out_dir / "feather.json", "w", encoding="utf-8") as f:
        json.dump(store_feed, f, indent=2, ensure_ascii=False)
    with open(out_dir / "store.json", "w", encoding="utf-8") as f:
        json.dump(store_feed, f, indent=2, ensure_ascii=False)

    with open(out_dir / "esign.json", "w", encoding="utf-8") as f:
        json.dump(generate_esign_json(apps, config, raw_esign_url, app_md_desc, developer_fallback=owner), f, indent=2, ensure_ascii=False)

    with open(out_dir / "scarlet.json", "w", encoding="utf-8") as f:
        json.dump(generate_scarlet_json(apps, config, app_md_desc, developer_fallback=owner), f, indent=2, ensure_ascii=False)

    print("Successfully generated all dynamic JSON feeds in /JSON")

if __name__ == "__main__":
    main()
