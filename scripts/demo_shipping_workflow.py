"""离线演示真实审批图：临时数据库，暂停、复查和重复批准去重。"""
import argparse
import json
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from business.order_repository import initialize_demo_orders
from business.shipping import submit_request, run_approval


def count(database):
    with closing(sqlite3.connect(database)) as db:
        return db.execute('SELECT COUNT(*) FROM shipping_tickets').fetchone()[0]


def setup(database):
    initialize_demo_orders(database)
    with closing(sqlite3.connect(database)) as db,db:
        db.execute("UPDATE orders SET payment_status='已付款', shipping_status='等待仓库发货', promised_ship_at=? WHERE order_id='A1004'",
            ((datetime.now(timezone.utc)-timedelta(hours=6)).isoformat(),))


def main(output):
    (ROOT/'work').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='approval-demo-',dir=ROOT/'work') as directory:
        database=Path(directory)/'happy.sqlite3'
        setup(database)
        row=submit_request('A1004','expedite',database=database)
        pending=run_approval(row['request_id'],database=database)
        pending_count=count(database)
        assert pending['status']=='pending' and pending_count==0
        approved=run_approval(row['request_id'],{'action':'approve','actor':'demo-reviewer'},database)
        repeated=run_approval(row['request_id'],{'action':'approve','actor':'demo-reviewer'},database)
        assert approved['status']=='completed' and approved['ticket_id']==repeated['ticket_id'] and count(database)==1
        result={'isolated_temp_database':True,'happy_path':{'pending_status':pending['status'],'tickets_before_approval':pending_count,
            'approved_status':approved['status'],'ticket_id':approved['ticket_id'],'repeat_ticket_id':repeated['ticket_id'],'tickets_after_repeat':count(database)}}
        changed=Path(directory)/'changed.sqlite3'
        setup(changed)
        row=submit_request('A1004','expedite',database=changed)
        run_approval(row['request_id'],database=changed)
        with closing(sqlite3.connect(changed)) as db,db:
            db.execute("UPDATE orders SET shipping_status='运输中' WHERE order_id='A1004'")
        invalidated=run_approval(row['request_id'],{'action':'approve','actor':'demo-reviewer'},changed)
        assert invalidated['status']=='invalidated' and count(changed)==0
        result['changed_before_approval']={'status':invalidated['status'],'message':invalidated['result_message'],'tickets':count(changed)}
    path=ROOT/output
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default='work/demo-shipping-result.json')
    main(parser.parse_args().output)
