import copy
import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import server
from role_b.service import Repository, FIXTURES, read


class RoleBHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start()
        cls.base='http://127.0.0.1:'+str(cls.http.server_port)
        cls.request=read(FIXTURES/'case-a/request.json')

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.thread.join()

    def call(self,path,data=None,origin=None):
        req=urllib.request.Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,
            headers={'Content-Type':'application/json',**({'Origin':origin} if origin else {})})
        try:
            with urllib.request.urlopen(req) as response:return response.status,json.loads(response.read())
        except urllib.error.HTTPError as error:
            try:value=json.loads(error.read())
            except (ValueError,UnicodeError):value={}
            return error.code,value

    def test_catalog_and_source_snapshots_exclude_actual_context(self):
        status,catalog=self.call('/api/role-b/catalog');self.assertEqual(status,200)
        self.assertEqual(len(catalog['cases']),3);self.assertEqual(len(catalog['matches']),40)
        self.assertEqual(catalog['totals']['covered'],91031)
        self.assertNotIn('actual_action_id',json.dumps(catalog))
        match=catalog['matches'][0]['match_id'];status,index=self.call('/api/role-b/decisions?match_id='+match)
        self.assertEqual(status,200);sid=index['records'][0]['snapshot_id']
        status,bundle=self.call('/api/role-b/snapshot?match_id='+match+'&snapshot_id='+sid)
        self.assertEqual(status,200);self.assertNotIn('retrospective',bundle)

    def test_analysis_has_computed_candidates_when_agents_are_unavailable(self):
        status,response=self.call('/api/role-b/analysis',self.request)
        self.assertEqual(status,200);self.assertEqual(response['status'],'model_unavailable')
        self.assertTrue(response['candidates']);self.assertEqual(response['agent_trace'],[])
        self.assertIsNone(response['explanation'])
        status,agent=self.call('/api/role-b/agents',{'request':self.request,'candidate_set_id':response['candidate_set_id']})
        self.assertEqual(status,200);self.assertEqual(agent,response)

    def test_routes_rollout_and_actual_overlay_are_bound_to_the_same_candidates(self):
        _,response=self.call('/api/role-b/analysis',self.request)
        body={'request':self.request,'candidate_set_id':response['candidate_set_id']}
        status,routes=self.call('/api/role-b/routes',body);self.assertEqual(status,200)
        self.assertEqual(routes['candidate_set_id'],response['candidate_set_id'])
        action=next(c['action_id'] for c in response['candidates'] if c['rollout_supported'])
        status,rollout=self.call('/api/role-b/rollout',{**body,'action_id':action})
        self.assertEqual(status,200);self.assertEqual(len(rollout['scenarios']),4)
        self.assertEqual(rollout['target'],next(r['target'] for r in routes['routes'] if r['action_id']==action))
        status,actual=self.call('/api/role-b/actual',body);self.assertEqual(status,200)
        self.assertEqual(actual['response']['candidate_set_id'],response['candidate_set_id'])
        self.assertEqual([c['rank'] for c in actual['response']['candidates']],[c['rank'] for c in response['candidates']])
        self.assertEqual(self.call('/api/role-b/rollout',{**body,'action_id':'hold:1s'})[0],400)
        self.assertEqual(self.call('/api/role-b/routes',{**body,'candidate_set_id':'outcome-2.0:0000000000000000'})[0],400)

    def test_request_shapes_clock_profiles_and_snapshot_paths_are_rejected(self):
        for update in [{'actor_id':'H99'},{'snapshot_id':'../secret'},{'t_ms':-1},{'question':''},{'profile':{'kind':'sofascore'}}]:
            self.assertIn(self.call('/api/role-b/analysis',{**self.request,**update})[0],(400,404))
        self.assertEqual(self.call('/api/role-b/analysis',{**self.request,'outcome':'goal'})[0],400)
        self.assertEqual(self.call('/api/role-b/snapshot?match_id=../.local&snapshot_id=bad')[0],404)
        self.assertEqual(self.call('/api/role-b/analysis',self.request,'https://evil.example')[0],403)
        self.assertEqual(self.call('/api/role-b/catalog',origin='https://evil.example')[0],403)

    def test_unknown_model_does_not_fall_back_to_enriched_v1(self):
        repo=Repository()
        with self.assertRaises(KeyError):repo.model_for('not-registered')
        request={**self.request,'match_id':'not-registered'}
        self.assertEqual(self.call('/api/role-b/analysis',request)[0],404)


if __name__=='__main__':unittest.main()
