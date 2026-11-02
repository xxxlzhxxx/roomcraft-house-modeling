"""Reference-image enhancement via the explicitly requested Seedream 5.0 Pro.

Load the user's Ark environment first. Private inventory values never leave memory
except for the authorized Ark request. No Blender operations are performed here.
"""
import argparse
import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request


def configuration():
    inventory = Path(os.environ.get('LLM_INVENTORY_FILE', str(Path.home()/'WorkSpace/LLM_env.md')))
    lines = inventory.read_text().splitlines()
    indices = [i for i, line in enumerate(lines)
               if re.sub(r'[^a-z0-9.]', '', line.lower()) == 'seedream5.0pro']
    if not indices:
        raise RuntimeError('Seedream 5.0 Pro is absent from the private inventory')
    # Resolve the named model and its associated account together, never a generic default.
    i = indices[0]
    ep = re.search(r'ep-[\w-]+', '\n'.join(lines[i+1:i+4]))
    keys = re.findall(r'(?im)API\s*key\s*[:：]\s*([^\s`]+)', '\n'.join(lines[:i]))
    if not ep or not keys:
        raise RuntimeError('Seedream private configuration is incomplete')
    return {'model': ep.group(0), 'api_key': keys[-1],
            'base_url': os.environ.get('ARK_BASE_URL', 'https://ark.cn-beijing.volces.com/api/v3').rstrip('/')}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--prompt', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--check-config', action='store_true')
    a = p.parse_args()
    cfg = configuration()
    if a.check_config:
        print(json.dumps({'modelLabel':'Seedream 5.0 Pro','inventoryMatch':True,'credentialPresent':bool(cfg['api_key'])}))
        return
    if a.output.exists() or a.output.with_suffix('.json').exists():
        raise SystemExit('Output already exists; use a new revision name')
    image = a.input.read_bytes()
    prompt = a.prompt.read_text()
    if not image.startswith(b'\x89PNG'):
        raise SystemExit('Expected a PNG reference image')
    payload = {'model':cfg['model'], 'prompt':prompt,
               'image':'data:image/png;base64,'+base64.b64encode(image).decode(),
               'size':'1536x1024', 'response_format':'b64_json',
               'output_format':'png', 'watermark':False}
    record = {'modelLabel':'Seedream 5.0 Pro', 'provider':'Volcengine Ark',
              'modelSelectedFromPrivateInventory':True, 'mode':'reference-image-edit',
              'input':str(a.input.resolve()), 'inputSha256':digest(image),
              'prompt':prompt, 'size':'1536x1024', 'outputFormat':'png',
              'hardGeometryControl':False, 'appliedTo3D':False,
              'createdAt':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    started=time.monotonic()
    req=urllib.request.Request(cfg['base_url']+'/images/generations',
        data=json.dumps(payload).encode(), method='POST',
        headers={'Authorization':'Bearer '+cfg['api_key'], 'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=360) as response:
            result=json.load(response)
            record['requestId']=response.headers.get('X-Request-Id') or response.headers.get('X-Tt-Logid')
        data=result.get('data',[])
        if len(data)!=1 or not data[0].get('b64_json'):
            raise RuntimeError('Response did not contain exactly one base64 image')
        output=base64.b64decode(data[0]['b64_json'],validate=True)
        if not output.startswith(b'\x89PNG'):
            raise RuntimeError('Returned image is not PNG')
        a.output.write_bytes(output)
        returned=result.get('model','')
        record.update(status='generated_pending_visual_review',output=str(a.output.resolve()),
                      outputSha256=digest(output), bytes=len(output),usage=result.get('usage'),
                      returnedPublicModel=returned if returned.startswith('doubao-') else None)
    except urllib.error.HTTPError as exc:
        # Never serialize provider error bodies: they may repeat private endpoint IDs.
        record.update(status='failed',httpStatus=exc.code)
        try:
            error=json.loads(exc.read()).get('error',{})
            code=str(error.get('code',''))
            record['errorCode']=code if re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',code) else 'provider_error'
        except Exception:
            pass
    except Exception as exc:
        record.update(status='failed',errorType=type(exc).__name__)
    record['elapsedSeconds']=round(time.monotonic()-started,2)
    a.output.with_suffix('.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:record.get(k) for k in ('status','modelLabel','output','httpStatus','errorCode','elapsedSeconds')},ensure_ascii=False))
    if record['status']=='failed':
        raise SystemExit(1)


if __name__=='__main__':
    main()
