"""Recovery exports already processed facts without continuing a paid step."""
import json

import pytest

from reno import config, db, llm, report, sync_reports, sync_snapshot, storage_lifecycle
from reno.sync_protocol import SyncError
from tests.test_sync_snapshot import source, export, video, PUBLICATION
from tests.test_report_lifecycle import store, connect, manifest


def test_recovery_omits_unfinished_video_and_whole_mixed_relations(source):
    con, media, _ = source
    con.execute("UPDATE video_asset SET status='done_atomize' WHERE video_id='v_2'")
    con.commit()
    original = {table: [tuple(row) for row in con.execute(f'SELECT * FROM "{table}"')]
                for table in ('video_asset', 'knowledge_atom', 'knowledge_cluster', 'conflict_case', 'evidence_ref')}
    with pytest.raises(SyncError, match='Unprocessed'):
        export(source)
    result = export(source, recovery_projection=True)
    assert [item['video_id'] for item in result['videos']] == ['v_1']
    assert result['globals'] == {'knowledge_cluster': [], 'conflict_case': []}
    atoms = video(result)['tables']['knowledge_atom']
    assert len(atoms) == 2 and all(atom['cluster_id'] is None for atom in atoms)
    assert {atom['claim'] for atom in atoms} == {'有证据的施工建议', '另一项施工建议'}
    assert len(video(result)['tables']['evidence_ref']) == 4
    assert all([tuple(row) for row in con.execute(f'SELECT * FROM "{table}"')] == original[table]
               for table in original)
    assert con.in_transaction
    with pytest.raises(SyncError, match='Unprocessed'):
        export(source)


def test_recovery_preserves_complete_relation_rows_and_their_derivation(source):
    result = export(source, recovery_projection=True)
    assert result == export(source)


def test_recovery_refuses_broken_source_graph_instead_of_masking_it(source):
    con, _, _ = source
    con.execute("UPDATE knowledge_cluster SET members_json='[\"atom_v_1\",\"extra_v_1\"]' WHERE cluster_id='cluster_main'")
    with pytest.raises(SyncError):
        export(source, recovery_projection=True)
    assert not con.execute("SELECT 1 FROM sqlite_temp_master WHERE name IN ('video_asset','knowledge_atom','knowledge_cluster','conflict_case')").fetchone()


def test_interrupted_real_atom_replacement_omits_historical_relations_without_judge(source):
    con, _, _ = source
    con.execute("UPDATE video_asset SET status='done_ocr' WHERE video_id='v_2'")
    con.commit()
    db.replace_atoms(con, 'v_2', [{'id': 'new_v2', 'claim': '尚未完成处理的新原子', 'polarity': 'recommend',
        'evidence_refs': [{'modality': 'asr', 'source_item_id': 'asr_0', 'start_ms': 0,
                           'end_ms': 100, 'evidence_text': '尚未完成处理的新证据'}]}])
    con.execute("UPDATE video_asset SET status='done_atomize' WHERE video_id='v_2'")
    con.commit()
    assert con.execute("SELECT 1 FROM knowledge_atom WHERE id='atom_v_2'").fetchone() is None
    original = con.execute("SELECT members_json FROM knowledge_cluster WHERE cluster_id='cluster_main'").fetchone()[0]
    result = export(source, recovery_projection=True)
    assert result['globals'] == {'knowledge_cluster': [], 'conflict_case': []}
    assert [item['video_id'] for item in result['videos']] == ['v_1']
    assert len(video(result)['tables']['knowledge_atom']) == 2
    assert con.execute("SELECT members_json FROM knowledge_cluster WHERE cluster_id='cluster_main'").fetchone()[0] == original
    assert con.execute("SELECT status FROM video_asset WHERE video_id='v_2'").fetchone()[0] == 'done_atomize'


def test_recovery_refuses_processed_atom_with_unknown_cluster(source):
    con, _, _ = source
    con.execute("UPDATE knowledge_atom SET cluster_id='missing' WHERE id='atom_v_1'")
    with pytest.raises(SyncError, match='missing source cluster'):
        export(source, recovery_projection=True)


@pytest.mark.parametrize('bad_video', [[], {}, True, 123])
def test_recovery_refuses_malformed_side_video_even_in_an_omitted_relation(source, bad_video):
    con, _, _ = source
    con.execute("UPDATE video_asset SET status='done_atomize' WHERE video_id='v_2'")
    side = json.loads(con.execute("SELECT side_b_json FROM conflict_case WHERE conflict_id='conflict_main'").fetchone()[0])
    side['video'] = bad_video
    con.execute("UPDATE conflict_case SET side_b_json=? WHERE conflict_id='conflict_main'", (json.dumps(side),))
    with pytest.raises(SyncError):
        export(source, recovery_projection=True)


def test_recovery_projection_never_supplements_missing_processed_media(source):
    con, media, _ = source
    con.execute("UPDATE video_asset SET status='done_atomize' WHERE video_id='v_2'")
    with pytest.raises(SyncError, match='media descriptors'):
        export(source, media={}, recovery_projection=True)


def test_recovery_refuses_cross_video_evidence_instead_of_exporting_different_facts(source):
    con, _, _ = source
    con.execute("UPDATE video_asset SET status='done_atomize' WHERE video_id='v_2'")
    con.execute("UPDATE evidence_ref SET video_id='v_2' WHERE atom_id='atom_v_1'")
    with pytest.raises(SyncError, match='Evidence belongs'):
        export(source, recovery_projection=True)


def test_recovery_refuses_missing_local_evidence_source(source):
    con, _, _ = source
    con.execute("UPDATE evidence_ref SET source_item_id='missing' WHERE atom_id='atom_v_1' AND modality='asr'")
    with pytest.raises(SyncError, match='Evidence source'):
        export(source, recovery_projection=True)


def test_recovery_report_scope_excludes_unfinished_claims_and_old_summary(store, monkeypatch):
    config.set_local({'node_role': 'all'})
    con = connect(store)
    try:
        for vid, status, claim in [('ready', 'processed', '已处理的施工事实'),
                                    ('pending', 'done_atomize', '未完成私有处理不应发布')]:
            con.execute("INSERT INTO video_asset(video_id,title,status) VALUES(?,?,?)", (vid, vid, status))
            db.replace_atoms(con, vid, [{'id': vid + '_atom', 'claim': claim, 'polarity': 'recommend', 'confidence': 0.8, 'dimension': 'acceptance',
                'category': 'waterproof', 'evidence_refs': [{'modality': 'asr', 'start_ms': 0,
                    'end_ms': 100, 'evidence_text': claim}]}])
        con.commit()
    finally:
        con.close()
    monkeypatch.setattr(llm, 'chat', lambda *a, **k: ('# 未完成私有处理不应发布的旧总结', 'fixture'))
    report.summary_report(profile=store)
    old_summary = manifest(store, 'summary')
    monkeypatch.setattr(report, 'summary_report', lambda *a, **k: pytest.fail('Recovery must not summarize with a model'))
    result = report.run_all(profile=store, recovery_projection=True)
    assert result['videos'] == 1 and result['atoms'] == 1
    for name in ('incremental_diff', 'checklist', 'conflicts', 'prices'):
        info = manifest(store, name)
        assert info['knowledge_scope'] == 'recovery_processed'
        text = report.read_report(name, profile=store)
        assert '仅包含已处理视频' in text and '未完成私有处理' not in text
    assert '已处理的施工事实' in report.read_report('checklist', profile=store)
    con = connect(store)
    try:
        assert con.execute("SELECT COUNT(*) FROM knowledge_atom").fetchone()[0] == 2
        assert con.execute("SELECT status FROM video_asset WHERE video_id='pending'").fetchone()[0] == 'done_atomize'
        with storage_lifecycle.shared(store):
            con.execute('BEGIN')
            projected = sync_reports.export_reports(con, profile=store, recovery_projection=True)
            assert {entry['name'] for entry in projected} == {'incremental_diff', 'checklist', 'conflicts', 'prices'}
            assert all('未完成私有处理' not in entry['content'] for entry in projected)
            assert {entry['name'] for entry in sync_reports.export_reports(con, profile=store)} == {'summary'}
            assert manifest(store, 'summary') == old_summary
    finally:
        con.close()
    assert report.metadata(profile=store)['checklist']['stale'] is True
    # A later change must still qualify the immutable projected report as
    # historical, in addition to its original processed-only scope notice.
    con = connect(store)
    try:
        con.execute("UPDATE knowledge_atom SET claim='后来修订的施工事实' WHERE id='ready_atom'")
        con.commit()
    finally:
        con.close()
    presented = report.present_report('checklist', profile=store)
    assert '历史报告' in presented and '已处理的施工事实' in presented
    assert '后来修订的施工事实' not in presented
    assert presented.count('仅包含已处理视频') == 1
