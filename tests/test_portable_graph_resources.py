"""Untrusted archive counters must not drive an allocation before rejection."""
from copy import deepcopy
import subprocess
import sys
from uuid import uuid4

import pytest
from backend import portable_codec as codec
from test_portable_financial_fixture import FIXTURE,decoded


@pytest.mark.parametrize('pages',[10**12,2**63-1])
def test_complete_interval_rejects_huge_missing_page_count_under_a_bounded_heap(tmp_path,pages):
    rows=decoded();columns=codec.TABLES['capture_intervals']['columns'];index=lambda name:columns.index(name)
    interval=deepcopy(next(row for row in rows['capture_intervals'] if row[index('state')]=='complete'))
    interval[index('id')]=str(uuid4());interval[index('total_pages')]=pages;interval[index('total_records')]=0
    rows['capture_intervals'].append(interval);rows['capture_intervals'].sort(key=lambda row:row[index('id')].encode())
    archive=tmp_path/'untrusted-count.zip'
    codec.create_archive(archive,rows,dataset_id=FIXTURE['dataset_id'],owners=FIXTURE['owner_ids'],
        session_id=str(uuid4()),archive_id=str(uuid4()),created_at='2026-10-20T00:00:00Z',version='resource-fixture')
    program='''
import resource,sys
resource.setrlimit(resource.RLIMIT_AS,(256*1024*1024,256*1024*1024))
from pathlib import Path
from backend import portable_codec as codec,portable_graph as graph
stage=codec.read_archive(Path(sys.argv[1]),Path(sys.argv[2]))
try:graph.validate_stage(stage)
except codec.PortableError as error:assert error.code=='incomplete_coverage',error.code
else:raise AssertionError('Incomplete interval accepted')
'''
    result=subprocess.run([sys.executable,'-c',program,str(archive),str(tmp_path/'stage')],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr


def test_oversized_json_fanout_rejects_before_allocating_an_unbounded_visit_stack():
    program='''
import resource
resource.setrlimit(resource.RLIMIT_AS,(256*1024*1024,256*1024*1024))
from backend import portable_codec as codec,portable_graph as graph
raw='['+'0,'*5000000+'0]'
try:graph.inspect_json(raw)
except codec.PortableError as error:assert error.code=='json_budget_exceeded',error.code
else:raise AssertionError('Oversized fanout accepted')
'''
    result=subprocess.run([sys.executable,'-c',program],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
