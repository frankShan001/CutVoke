"""Run the existing person compositor inside its installed private runtime."""
import argparse
import json
from pathlib import Path
from cutvoke.rendering.person_cutout import generate_person_cutout
from cutvoke.storage.extension_paths import atomic_json


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--request',required=True);args=parser.parse_args()
    request=json.loads(Path(args.request).read_text('utf8'))
    def report(phase,percent):atomic_json(request['progressPath'],{'phase':phase,'progress':percent})
    result=generate_person_cutout(**request['arguments'],progress=report)
    atomic_json(request['resultPath'],result)


if __name__=='__main__':main()
