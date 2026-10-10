#!/usr/bin/env python3
"""Make a draft stock-payload restore update; not a brick-recovery mechanism."""
import argparse
import copy
import hashlib
import importlib.util
import io
import json
import pathlib
import tarfile

BASE=pathlib.Path(__file__).resolve().parent
PROJECT=BASE.parent
ORIGINAL_SHA256='1ccb4667e3e9bdf9a35be4dc58d09b898ca53c5fd58396b7667fb91fe895f925'
INFO='firmware_v20.info'
KERNEL='recovery-update/xImage'
ROOTFS='recovery-update/rootfs.squashfs'


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original',type=pathlib.Path,default=PROJECT/'inputs/update.bin')
    parser.add_argument('--analysis',type=pathlib.Path,default=PROJECT/'.work/analysis-v2.1')
    parser.add_argument('--output',type=pathlib.Path,required=True)
    parser.add_argument('--version',default='V2.1-ND-restore1')
    args=parser.parse_args()
    args.original=args.original.resolve();args.output=args.output.resolve()
    if digest(args.original)!=ORIGINAL_SHA256:raise ValueError('Original firmware hash mismatch')
    if not args.version.startswith('V2.1-ND-restore') or '\n' in args.version or '\r' in args.version:raise ValueError('Use a distinct V2.1-ND-restore development manifest version')
    if args.output.exists():raise ValueError('Output already exists; use a new directory')
    spec=importlib.util.spec_from_file_location('inspect_update',args.analysis/'inspect_update.py');inspector=importlib.util.module_from_spec(spec);spec.loader.exec_module(inspector)
    original_validation=inspector.inspect(args.original)
    with tarfile.open(args.original) as source:
        members=list(source)
        files={item.name:source.extractfile(item).read() for item in members if item.isfile()}
    info=files[INFO].decode().splitlines()
    if len(info)!=4 or info[1]!='V2.1':raise ValueError('Unexpected original manifest')
    info[1]=args.version
    files[INFO]=('\n'.join(info)+'\n').encode()
    args.output.mkdir(parents=True)
    package=args.output/('update-'+args.version+'-RESTORE-DRAFT.bin')
    with tarfile.open(package,'w',format=tarfile.GNU_FORMAT) as destination:
        for item in members:
            item=copy.copy(item)
            if item.isfile():
                item.size=len(files[item.name]);destination.addfile(item,io.BytesIO(files[item.name]))
            else:destination.addfile(item)
    result=inspector.inspect(package)
    with tarfile.open(package) as restored:
        payload_byte_identical={name:restored.extractfile(name).read()==files[name] for name in (KERNEL,ROOTFS)}
    if not all(payload_byte_identical.values()):raise ValueError('Restore payload changed')
    if result['payloads']!=original_validation['payloads']:raise ValueError('Restore payload digest/size changed')
    if digest(args.original)!=ORIGINAL_SHA256:raise ValueError('Original changed while packaging')
    report=dict(status='restore-draft-offline-validated',original_sha256=ORIGINAL_SHA256,original_unchanged=True,payload_byte_identical=payload_byte_identical,only_manifest_content_change='Version line V2.1 → '+args.version,original_manifest_md5_lines_preserved=True,package=result,
                limitations=['Requires a working firmware-update UI and accepted development manifest version','Not bootloader recovery and cannot recover a device that does not boot','Actual recovery acceptance, flashing and restored boot unverified'])
    (args.output/'validation.json').write_text(json.dumps(report,indent=2)+'\n')
    (args.output/'RESTORE-DRAFT-NOT-BRICK-RECOVERY.txt').write_text('The original kernel and rootfs bytes are unchanged. Only the manifest version differs to avoid the stock UI same-version check. This draft still requires a functioning firmware update UI and recovery acceptance; it is not a method to recover an unbootable device. No update has been installed or flashed.\n')
    print(json.dumps(dict(package=str(package),validation=str(args.output/'validation.json'),passed=True),indent=2))

if __name__=='__main__':main()
