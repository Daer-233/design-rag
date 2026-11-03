import argparse, json, os, urllib.request

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--project'); parser.add_argument('--mode',default='all'); args=parser.parse_args()
    base=os.getenv('DESIGN_RAG_API_BASE_URL','http://127.0.0.1:8030').rstrip('/')
    payload={'project_id':args.project,'run_type':args.mode}
    req=urllib.request.Request(base+'/api/v1/admin/harness/runs',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(req) as response: print(json.dumps(json.load(response),ensure_ascii=False,indent=2))
if __name__=='__main__': main()