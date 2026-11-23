"""Start the local backend with an environment key or a non-echoing terminal input."""
import argparse
import getpass
import os
from pathlib import Path
import runpy
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8783)
    args=parser.parse_args()
    if not os.environ.get('DEEPSEEK_API_KEY'):
        if not sys.stdin.isatty():raise SystemExit('Set the backend key in the environment or use an interactive terminal.')
        os.environ['DEEPSEEK_API_KEY']=getpass.getpass('Backend API key: ')
    sys.argv=['web','--port',str(args.port)]
    runpy.run_module('chinatravel.personalization.web',run_name='__main__')


if __name__=='__main__':main()
