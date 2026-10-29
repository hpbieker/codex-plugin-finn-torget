import asyncio
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

root = Path(__file__).resolve().parent.parent
platform = 'hygglo' if (root / 'hygglo_mcp.py').exists() else 'finn-torget'
filename = 'hygglo_mcp.py' if platform == 'hygglo' else 'finn_torget_mcp.py'
if True:
    # Platform helpers have colliding module names.
    for name in ('listing_fields','listing_fulltext','listing_text_query'):
        sys.modules.pop(name,None)
    spec=importlib.util.spec_from_file_location(platform.replace('-','_')+'_test',root/filename)
    mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod)
    ids=['123','456'] if platform=='finn-torget' else [123,456]
    rows=[{'listing_id':ids[0],'description':'A full replacement description.'},{'listing_id':ids[1],'title':'New title'}]
    target='update_listing' if platform=='finn-torget' else '_update_listing'
    seen=[]
    def update(**kwargs):
        seen.append(kwargs)
        if kwargs['listing_id']==ids[1]:
            raise RuntimeError('secret message must not leak')
        return {'verified':True,'outcome':'verified','public_verified':True,'product':{'description':'large text'}}
    from contextlib import nullcontext
    session_patch=patch.object(mod.product_api,'HyggloSession') if platform=='hygglo' else nullcontext()
    with session_patch as session, patch.object(mod,target,side_effect=update) as writer:
        result=mod.update_listings_many(rows,verify_public=True)
        assert result['verified']==1 and result['unconfirmed']==1,result
        assert [r['listing_id'] for r in result['results']]==ids
        assert 'secret' not in str(result) and 'large text' not in str(result)
        assert 'title' not in seen[0] and 'description' not in seen[1],seen
        assert all(r['verify_public'] for r in seen)
        if platform=='hygglo':
            session.assert_called_once_with()
            assert all(r['_session'] is session.return_value for r in seen)
        for invalid in ([], rows*26, [rows[0],rows[0]], [rows[0],{'listing_id':ids[1]}],
                        [rows[0],{'listing_id':ids[1],'unexpected':'x'}],
                        [rows[0],{'listing_id':ids[1],'title':'Valid title','image_paths':[]}],
                        [rows[0],{'listing_id':ids[1],'price' if platform=='finn-torget' else 'price_1d':-1}]):
            writer.reset_mock()
            try:mod.update_listings_many(invalid)
            except (ValueError,TypeError):pass
            else:raise AssertionError(invalid)
            writer.assert_not_called()
    tools=asyncio.run(mod.mcp.list_tools())
    schema=next(t.inputSchema for t in tools if t.name=='update_listings_many')
    assert set(schema['properties'])=={'updates','verify_public'},schema
    assert schema['properties']['updates']['maxItems']==50
    assert schema['$defs']['ListingUpdate']['additionalProperties'] is False
    assert schema['$defs']['ListingUpdate']['required']==['listing_id']
    assert 'image_paths' not in schema['$defs']['ListingUpdate']['properties']
    single = next(t.inputSchema for t in tools if t.name == 'update_listing')
    assert 'image_paths' in single['properties']
    print(platform+': validation, patch isolation, partial failure, order, shared session, and MCP schema passed')
