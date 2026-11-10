import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import common
import engine

class FormatTests(unittest.TestCase):
    def setUp(self):self.rows=common.load_translations()
    def test_snapshot_and_format(self):
        self.assertEqual(len(self.rows),1317)
        self.assertEqual(common.translation_package(self.rows)['entries'],self.rows)
    def test_placeholders_and_tags(self):
        row=next(r for r in self.rows if '{type}' in r['source'])
        bad=copy.deepcopy(self.rows);next(r for r in bad if r['id']==row['id'])['translation']='자리표시자 없음'
        with self.assertRaises(ValueError):common.validate_rows(bad,self.rows)
        with self.assertRaises(ValueError):common.validate_rows([{'id':'x','source':'<code>{n}</code>','translation':'{n}'}])
    def test_import_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'ko.json';common.atomic_json(p,common.translation_package(self.rows))
            self.assertEqual(common.load_translations(p),self.rows)
            bad=common.translation_package(self.rows);bad['game_sha256']='0'*64;common.atomic_json(p,bad)
            with self.assertRaises(ValueError):common.load_translations(p)
        with self.assertRaises(ValueError):common.validate_rows(self.rows[:-1],self.rows)
    def test_unknown_file_rejected(self):
        with self.assertRaises(engine.PatchError):engine.recover_original(b'unknown game')
    def test_reviewer_save_reload_export(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'LRKO_WORK_DIR':tmp}):
            spec=importlib.util.spec_from_file_location('test_reviewer',ROOT/'src/reviewer.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
            app=m.Workbench();app.withdraw()
            try:
                app.update();app.trans.delete('1.0','end');app.trans.insert('1.0','새로운 {type}');app.update()
                self.assertTrue(app.save())
                saved=common.read_json(Path(tmp)/'data/catalog.json')
                self.assertEqual(saved[0]['translation'],'새로운 {type}')
                package=common.translation_package(saved);p=Path(tmp)/'reviewed.json';common.atomic_json(p,package)
                self.assertEqual(common.load_translations(p)[0]['translation'],'새로운 {type}')
                app.add_issues([{'source':'New {type}','location':'메뉴'},{'source':'Unseen text','location':'지도'}])
                self.assertIsNotNone(app.qa[0]['catalog_id']);self.assertIsNone(app.qa[1]['catalog_id'])
            finally:app.destroy()

@unittest.skipUnless(os.environ.get('LRKO_TEST_ORIGINAL'),'set LRKO_TEST_ORIGINAL to a privately held original executable')
class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original=Path(os.environ['LRKO_TEST_ORIGINAL']).read_bytes();cls.rows=common.load_translations()
        assert common.sha(cls.original)==engine.manifest()['sha256']
    def test_build_recover_and_modified_translation(self):
        patched,report=engine.build(self.original,self.rows)
        self.assertEqual(engine.recover_original(patched),self.original)
        again,_=engine.build(self.original,self.rows);self.assertEqual(patched,again)
        changed=copy.deepcopy(self.rows);next(r for r in changed if r['source']=='Character')['translation']='등장인물'
        other,_=engine.build(self.original,changed);self.assertNotEqual(other,patched)
        self.assertEqual(engine.recover_original(other),self.original)
        corrupted=bytearray(patched);corrupted[2048]^=1
        with self.assertRaises(engine.PatchError):engine.recover_original(bytes(corrupted))
    def test_real_files_apply_reapply_restore(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(engine,'game_running',return_value=False):
            game=Path(tmp)/'local-realm.exe';game.write_bytes(self.original)
            world=Path(tmp)/'world.db';world.write_bytes(b'user world must stay identical')
            report=engine.install(game,self.rows)
            self.assertEqual(common.sha(game.read_bytes()),report['patched'])
            backup=Path(report['backup']);self.assertEqual(backup.read_bytes(),self.original)
            engine.install(game,self.rows);engine.install(game,restore=True)
            self.assertEqual(game.read_bytes(),self.original);self.assertEqual(world.read_bytes(),b'user world must stay identical')
            backup.write_bytes(b'bad backup')
            with self.assertRaises(engine.PatchError):engine.install(game,self.rows)
            self.assertEqual(game.read_bytes(),self.original)
    def test_reject_running_game_and_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            game=Path(tmp)/'local-realm.exe';game.write_bytes(self.original)
            with patch.object(engine,'game_running',return_value=True):
                with self.assertRaises(engine.PatchError):engine.install(game,self.rows)
            self.assertEqual(game.read_bytes(),self.original)
            changed=bytearray(self.original);changed[2048]^=1;game.write_bytes(changed)
            with patch.object(engine,'game_running',return_value=False):
                with self.assertRaises(engine.PatchError):engine.install(game,self.rows)
            self.assertEqual(game.read_bytes(),changed)
    def test_game_started_during_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            game=Path(tmp)/'local-realm.exe';game.write_bytes(self.original)
            with patch.object(engine,'game_running',side_effect=[False,True]):
                with self.assertRaises(engine.PatchError):engine.install(game,self.rows)
            self.assertEqual(game.read_bytes(),self.original)
            self.assertFalse(list(Path(tmp).glob('local-realm-ko-*.tmp')))

if __name__=='__main__':unittest.main()
