"""Offline UI regression: synthetic API responses; no server, worker or external requests."""
import json
from pathlib import Path
from playwright.sync_api import expect
from playwright.sync_api import sync_playwright


def run():
    static = Path(__file__).resolve().parents[1] / 'src/google_maps_scraper_kit/static'
    campaign = {'campaign_id': 'synthetic', 'name': 'Estúdios de pilates · Curitiba',
                'status': 'completed', 'result_count': 3, 'target_count': 3,
                'created_at': '2026-11-03T12:00:00Z',
                'jobs': [], 'read_only': False, 'config': {
                    'business_terms': ['estúdio de pilates'], 'location': {'city': 'Curitiba, PR'},
                    'search_area': {'latitude': -25.43, 'longitude': -49.27, 'radius_m': 3000},
                    'collect_emails': True, 'collect_socials': True, 'required_fields': []}}
    rows = [{'company_id': str(i), 'name': name, 'category': 'Estúdio de pilates',
             'phone': '(41) 3000-0000', 'emails': 'contato@example.org' if i < 2 else '',
             'website': 'https://example.org', 'whatsapp': 'https://wa.me/554130000000',
             'instagram': 'https://instagram.com/example', 'maps_url': 'https://www.google.com/maps?cid=synthetic', 'review_rating': '4.8',
             'review_count': 20, 'selected': 1, 'review_status': 'pending',
             'address': 'Endereço ilustrativo · Curitiba, PR'}
            for i, name in enumerate(['Studio Exemplo Centro', 'Pilates Exemplo Batel', 'Espaço Exemplo'])]
    other = dict(campaign, campaign_id='other', name='Outra busca sintética')
    campaigns = [campaign, other]
    errors, blocked, mutations = [], [], []
    export_requests = []
    delete_state = {'mode': 'success', 'held': []}
    runtime_status = {'maps_available': False, 'worker_enabled': False}
    review_mode, held = 'success', []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel='msedge')
        page = browser.new_page(viewport={'width': 1440, 'height': 1050})
        page.add_init_script("Object.defineProperty(navigator, 'clipboard', {value:{writeText:async text=>{window.copiedText=text;}}})")
        page.add_init_script("window.setInterval=()=>0")  # Poll deterministically via refresh() below.

        def route(request):
            url = request.request.url
            if not url.startswith('http://127.0.0.1:9899/'):
                blocked.append(url)
                request.abort()
                return
            path = url.split('9899', 1)[1]
            method = request.request.method
            if method == 'POST' and path == '/api/v1/campaigns':
                mutations.append((path, request.request.post_data_json))
                assert request.request.post_data_json['manual_approval_required'] is True
                request.fulfill(body=json.dumps({'campaign_id': 'synthetic'}), content_type='application/json')
                return
            if method == 'POST' and path == '/api/v1/campaigns/synthetic/approve':
                mutations.append((path, request.request.post_data_json))
                campaign['status'] = 'queued'
                request.fulfill(body=json.dumps(campaign), content_type='application/json')
                return
            if method == 'POST' and path == '/api/v1/campaigns/synthetic/cancel':
                mutations.append((path, request.request.post_data_json))
                campaign['status'] = 'cancelled'
                request.fulfill(body='{}', content_type='application/json')
                return
            if method == 'DELETE' and path.startswith('/api/v1/campaigns/'):
                mutations.append((path, None))
                if delete_state['mode'] == 'hold':
                    delete_state['held'].append(request)
                    return
                if delete_state['mode'] == 'missing':
                    request.fulfill(status=404, body=json.dumps({'error': 'campaign not found'}), content_type='application/json')
                    return
                if delete_state['mode'] == 'active':
                    request.fulfill(status=409, body=json.dumps({'error': 'campaign has active jobs'}), content_type='application/json')
                    return
                campaign_id = path.rsplit('/', 1)[1]
                campaigns[:] = [item for item in campaigns if item['campaign_id'] != campaign_id]
                request.fulfill(status=204, body='')
                return
            if method == 'PATCH':
                mutations.append((path, request.request.post_data_json))
                if review_mode == 'hold':
                    held.append(request)
                    return
                if review_mode == 'fail':
                    request.fulfill(status=500, body=json.dumps({'error': 'Falha sintética de revisão'}), content_type='application/json')
                    return
                row = next(row for row in rows if row['company_id'] == path.rsplit('/', 1)[1])
                row['review_status'] = request.request.post_data_json['review_status']
                request.fulfill(body='{}', content_type='application/json')
                return
            if '/export' in path:
                export_requests.append(path)
                extension = 'xlsx' if 'format=xlsx' in path else 'json' if 'format=json' in path else 'csv'
                request.fulfill(body='synthetic export', headers={'Content-Disposition': 'attachment; filename="synthetic.' + extension + '"'})
                return
            if path.startswith('/api/v1/geocode?'):
                request.fulfill(body=json.dumps({'latitude': -25.43, 'longitude': -49.27,
                                                 'label': 'Curitiba, Paraná, Brasil'}), content_type='application/json')
                return
            assert method == 'GET', 'Unexpected mutation: ' + method + ' ' + path
            assets = {'/': ('index.html', 'text/html'), '/app.css': ('app.css', 'text/css'),
                      '/app.js': ('app.js', 'application/javascript')}
            if path in assets:
                name, mime = assets[path]
                request.fulfill(body=(static / name).read_text(encoding='utf-8'), content_type=mime)
                return
            data = {'/api/v1/campaigns': {'campaigns': campaigns},
                    '/api/v1/runtime': runtime_status,
                    '/api/v1/campaigns/synthetic': campaign,
                    '/api/v1/campaigns/synthetic/results': {'results': rows},
                    '/api/v1/campaigns/other': other,
                    '/api/v1/campaigns/other/results': {'results': rows}}
            assert path in data, path
            request.fulfill(body=json.dumps(data[path]), content_type='application/json')

        page.route('**/*', route)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://127.0.0.1:9899/')
        assert page.title() == 'Google Maps Scraper'
        expect(page.locator('#notice')).to_be_hidden()
        assert page.get_by_role('heading', name='Google Maps Scraper', exact=True).count() == 1
        assert page.locator('.metric-companies .metric-icon').count() == 1
        assert page.locator('.metric-phones .metric-icon').count() == 1
        assert page.locator('.metric-socials .metric-icon').count() == 1
        assert page.get_by_role('link', name='Estrela', exact=True).get_attribute('href') == 'https://github.com/gosom/google-maps-scraper'
        assert page.get_by_role('link', name='Apoiar', exact=True).get_attribute('href') == 'https://github.com/sponsors/gosom'
        assert page.locator('#expand-map').count() == 0
        page.get_by_role('button', name='Abrir Estúdios de pilates · Curitiba').click()
        page.wait_for_function("document.querySelector('#stat-companies').textContent === '3'")
        assert page.locator('#overview .metrics > div').count() == 5
        assert page.locator('.history-item.active .history-meta').inner_text().startswith('Concluída')
        assert page.locator('.history-item.active .history-count strong').inner_text() == '3'
        assert page.locator('.history-count').first.evaluate("node=>getComputedStyle(node).textAlign") == 'center'
        expect(page.locator('#email-note')).to_be_visible()
        assert page.locator('#stat-emails').inner_text() == '2'
        assert page.locator('#campaign-metrics > div').count() == 6
        assert page.locator('#result-websites').inner_text() == '3'
        assert page.locator('#result-whatsapp').inner_text() == '3'
        assert '4,8' in page.locator('#result-rating').inner_text()
        page.evaluate("results.push({...results[0],company_id:'duplicate'});renderResults()")
        assert page.locator('#results tr').count() == 3
        page.locator('#dedupe-only').uncheck()
        assert page.locator('#results tr').count() == 4
        page.locator('#dedupe-only').check()
        page.evaluate("results.pop();renderResults()")
        assert page.locator('thead select').count() == 0
        assert page.locator('#results .maps-cell a').count() == 3
        assert page.locator('thead').inner_text().split() == ['Empresa', 'Contato', 'Nota', 'Endereço', 'Google', 'Maps']
        assert page.locator('#results tr').first.locator('td').count() == 5
        assert page.locator('[data-section="results-panel"]').get_attribute('aria-current') == 'location'
        expect(page.locator('#cancel')).to_be_hidden()
        expect(page.locator('#review-tools')).to_be_hidden()
        assert page.locator('#results input:checked').count() == 0  # Automatic target selection is not manual review.
        assert page.locator('thead input').count() == 0
        expect(page.locator('#results input').first).to_be_hidden()
        assert page.locator('#results .whatsapp-link').count() == 3
        assert page.locator('#show-map').inner_text() == 'Ver no Mapa'
        assert page.locator('#close-results').count() == 0
        assert page.locator('#history-runtime').count() == 0
        assert page.locator('#campaign-status .campaign-state').inner_text() == 'Concluída'
        assert page.locator('#campaign-status').inner_text().endswith('3 empresas')
        page.locator('#clear-overview').click()
        expect(page.locator('#stat-companies')).to_have_text('—')
        assert page.locator('.history-item.active').count() == 0
        expect(page.locator('#terms')).to_have_value('')
        expect(page.locator('#location')).to_have_value('Nova Friburgo, RJ')
        expect(page.locator('#count')).to_have_value('60')
        assert page.evaluate('center') is None
        expect(page.locator('#center-label')).to_have_text('Localize a cidade ou escolha o centro no mapa.')
        page.evaluate("load('synthetic')")
        expect(page.locator('#stat-companies')).to_have_text('3')
        page.locator('#refresh').click()
        expect(page.locator('.history-item.active')).to_have_attribute('data-campaign-id', 'synthetic')
        expect(page.locator('#stat-companies')).to_have_text('3')
        page.locator('#manual-review summary').click()
        expect(page.locator('#results input').first).to_be_visible()
        page.locator('#results input').first.focus()
        page.keyboard.press('Space')
        expect(page.locator('#results input').first).to_be_focused()
        expect(page.locator('#review-tools')).to_be_visible()
        assert page.locator('#reject-selected').evaluate("node=>getComputedStyle(node).borderRadius") == '18px'
        assert page.locator('#select-all').evaluate('(node)=>node.indeterminate')
        assert '1 empresa' in page.locator('#manual-count').inner_text()
        assert page.locator('#results tr.manual-selected').count() == 1
        page.evaluate('refresh()')
        assert page.locator('#results input:checked').count() == 1
        page.locator('#filter').fill('Espaço Exemplo')
        assert page.locator('#select-all').is_checked()
        page.locator('#filter').fill('')
        assert page.locator('#select-all').evaluate('(node)=>node.indeterminate')

        review_mode = 'hold'
        page.locator('#approve-selected').click()
        page.wait_for_function('mutating')
        assert len(held) == 1
        expect(page.locator('#search')).to_be_disabled()
        expect(page.locator('#results input').first).to_be_disabled()
        expect(page.locator('#refresh')).to_be_disabled()
        expect(page.locator('.history-open').last).to_be_disabled()
        assert page.locator('#results-panel').get_attribute('aria-busy') == 'true'
        page.evaluate("review('approved')")
        assert len(mutations) == 1  # Duplicate submission is ignored.
        request = held.pop()
        row = next(row for row in rows if row['company_id'] == mutations[-1][0].rsplit('/', 1)[1])
        row['review_status'] = 'approved'
        request.fulfill(body='{}', content_type='application/json')
        expect(page.locator('#result-feedback')).to_have_text('Revisão salva.')
        expect(page.locator('#review-tools')).to_be_hidden()
        assert page.locator('#results input:checked').count() == 0
        assert page.locator('#results').inner_text().count('Aprovado') == 1
        assert all(row['selected'] == 1 for row in rows)

        review_mode = 'fail'
        page.locator('#results input').first.check()
        page.locator('#reject-selected').click()
        expect(page.locator('#result-feedback')).to_have_text('Falha sintética de revisão')
        expect(page.locator('#approve-selected')).to_be_enabled()
        expect(page.locator('#search')).to_be_enabled()
        assert page.locator('#results input:checked').count() == 1
        review_mode = 'success'
        page.locator('#pending-selected').click()
        expect(page.locator('#result-feedback')).to_have_text('Revisão salva.')
        assert all(row['review_status'] == 'pending' for row in rows)
        page.locator('#select-all').check()
        assert page.locator('#results input:checked').count() == 3
        page.locator('#select-all').uncheck()
        expect(page.locator('#review-tools')).to_be_hidden()

        for status in ['pending_approval', 'queued', 'running', 'waiting', 'completed', 'partial', 'failed', 'blocked', 'cancelled']:
            campaign['status'] = status
            page.evaluate("load('synthetic')")
            assert page.locator('#cancel').is_visible() == (status in ['pending_approval', 'queued', 'running', 'waiting'])
            assert page.locator('#approve').is_visible() == (status == 'pending_approval')
        campaign['read_only'] = True
        campaign['status'] = 'running'
        page.evaluate("load('synthetic')")
        expect(page.locator('#cancel')).to_be_hidden()
        expect(page.locator('#results input').first).to_be_disabled()
        campaign['read_only'], campaign['status'] = False, 'completed'
        page.evaluate("load('synthetic')")
        campaign['status'] = 'pending_approval'
        page.evaluate("load('synthetic')")
        page.locator('#approve').click()
        expect(page.locator('#result-feedback')).to_contain_text('Worker desligado')
        expect(page.locator('#approve')).to_be_enabled()
        campaign['status'] = 'queued'
        page.evaluate("load('synthetic')")
        page.once('dialog', lambda dialog: dialog.accept())
        page.locator('#cancel').click()
        expect(page.locator('#cancel')).to_be_hidden()
        expect(page.locator('#result-feedback')).to_have_text('Estado atualizado.')
        assert mutations[-1][0].endswith('/cancel')
        campaign['status'] = 'completed'
        page.evaluate("load('synthetic')")
        for button, extension in [('export-xlsx', 'xlsx'), ('export', 'csv'), ('export-json', 'json')]:
            with page.expect_download() as download:
                page.locator('#' + button).click()
            assert download.value.suggested_filename == 'synthetic.' + extension
            assert export_requests[-1].count('company_id=') == 3
        page.locator('#filter').fill('Espaço Exemplo')
        with page.expect_download():
            page.locator('#export-xlsx').click()
        assert export_requests[-1].count('company_id=') == 1
        page.locator('#filter').fill('')
        page.locator('#results input').first.check()
        page.locator('#copy').click()
        expect(page.locator('#copy')).to_have_text('Copiado ✓')
        expect(page.locator('#result-feedback')).to_have_text('Empresas selecionadas copiadas.')
        assert len(page.evaluate('window.copiedText').splitlines()) == 1
        with page.expect_download():
            page.locator('#export-json').click()
        assert export_requests[-1].count('company_id=') == 1
        page.locator('#results input').first.uncheck()
        page.locator('#copy').click()
        expect(page.locator('#result-feedback')).to_have_text('Resultados visíveis copiados.')
        assert len(page.evaluate('window.copiedText').splitlines()) == 3
        page.evaluate("()=>{navigator.clipboard.writeText=async()=>{throw Error('denied')};}")
        page.locator('#copy').click()
        expect(page.locator('#result-feedback')).to_contain_text('Use o download CSV')
        assert page.locator('#save').count() == 0
        assert page.locator('#search').inner_text() == 'Iniciar busca'
        runtime_status.update(maps_available=True, worker_enabled=True)
        campaign['status'] = 'completed'
        page.locator('#terms').fill('estúdio de pilates')
        page.locator('#location').fill('Curitiba, PR')
        page.evaluate("chooseCenter({lat:-25.43,lng:-49.27})")
        page.locator('#search').click()
        expect(page.locator('#notice')).to_contain_text('Busca iniciada')
        assert mutations[-2][0] == '/api/v1/campaigns'
        assert mutations[-1][0].endswith('/approve')
        runtime_status.update(maps_available=False, worker_enabled=False)
        campaign['status'] = 'completed'
        page.evaluate("load('synthetic')")
        assert page.locator('#view-panel').count() == 0
        assert page.get_by_role('button', name='Visão geral').count() == 1
        page.get_by_role('button', name='Visão geral').click()
        assert page.get_by_role('button', name='Visão geral').get_attribute('aria-current') == 'location'
        assert page.locator('#results tr').count() == 3
        assert page.locator('.execution-details').count() == 1
        assert page.get_by_text('Detalhes da execução', exact=True).count() == 0
        page.get_by_role('button', name='Mapa', exact=True).click()
        expect(page.locator('#map-controls-slot')).to_be_visible()
        expect(page.locator('#map-leads')).to_be_hidden()
        page.set_viewport_size({'width': 739, 'height': 892})
        assert page.locator('#new-search').evaluate("node=>getComputedStyle(node).paddingLeft") == '8px'
        assert page.locator('#count').evaluate("node=>getComputedStyle(node).fontSize") == page.locator('[data-count]').first.evaluate("node=>getComputedStyle(node).fontSize")
        assert page.locator('#terms').bounding_box()['x'] - page.locator('#new-search').bounding_box()['x'] >= 8
        assert page.locator('#history-card').evaluate("node=>getComputedStyle(node).paddingLeft") == '8px'
        expect(page.locator('.history-count').first).to_be_hidden()
        assert page.locator('.history-item').evaluate_all("nodes=>nodes.every(node=>node.scrollWidth<=node.clientWidth)")
        form_output = Path('C:/Users/User/.codex/visualizations/2026/11/02/01a199a0-21c5-7191-8e8b-6d42ad609edb/map-form-spacing.png')
        page.screenshot(path=str(form_output))
        page.set_viewport_size({'width': 1440, 'height': 1050})
        page.get_by_role('button', name='Painel', exact=True).click()
        assert page.locator('[data-section="new-search"]').get_attribute('aria-current') == 'location'
        page.locator('#show-map').click()
        assert page.locator('#back-search').count() == 0
        page.get_by_role('button', name='Mapa', exact=True).click()
        expect(page.locator('#map-controls-slot')).to_be_visible()
        page.get_by_role('button', name='Painel', exact=True).click()
        assert page.locator('[data-section="new-search"]').get_attribute('aria-current') == 'location'
        for section in ['overview', 'new-search', 'history-card', 'results-panel']:
            page.locator('[data-section="' + section + '"]').click()
            assert page.locator('[data-section="' + section + '"]').get_attribute('aria-current') == 'location'
        page.locator('.history-open').last.click()
        expect(page.locator('.history-item.active')).to_have_attribute('data-campaign-id', 'other')
        page.locator('.history-open').first.click()
        expect(page.locator('.history-item.active')).to_have_attribute('data-campaign-id', 'synthetic')
        # Exercise the popup navigation handler with a local map double; never fetch tiles.
        page.evaluate("""()=>{
          window.mapCalls={fits:[],views:[],pans:[]};
          window.mapState={center:{lat:0,lng:0},zoom:13};
          map={getCenter:()=>mapState.center,getZoom:()=>mapState.zoom,
            setView:(point,zoom)=>{mapState.center=point;mapState.zoom=zoom;mapCalls.views.push([point,zoom]);},
            fitBounds:(points,options)=>mapCalls.fits.push({points,options}),
            panInside:(point,options)=>mapCalls.pans.push({point,options}),invalidateSize:()=>{},removeLayer:()=>{}};
          markers={clearLayers:()=>{}};
          window.L={latLngBounds:points=>points,divIcon:options=>options,
            circle:position=>({addTo(){return this;},setLatLng(){return this;},setRadius(){return this;},getBounds(){return {savedArea:position};}}),
            marker:(position,options={})=>({icon:options.icon,bindPopup(node){this.popup=node;return this;},addTo(){return this;},on(){return this;},setLatLng(){return this;},setIcon(icon){this.icon=icon;},getLatLng(){return position;},openPopup(){if(this.popup){this.popup.style.cssText='position:absolute;left:350px;top:40px';document.querySelector('#map').replaceChildren(this.popup);}return this;}}),
            circleMarker:position=>({
            bindPopup(node){this.popup=node;return this;},addTo(){return this;},on(){return this;},
            getLatLng(){return position;},setStyle(style){this.style=style;},openPopup(){this.popup.style.cssText='position:absolute;left:350px;top:40px';document.querySelector('#map').replaceChildren(this.popup);return this;}
          })};
          results[0].latitude=-25.43;results[0].longitude=-49.27;renderResults();
        }""")
        page.locator('#results .company-focus').click()
        expect(page.get_by_role('button', name='Ver detalhes', exact=True)).to_be_visible()
        page.get_by_role('button', name='Ver detalhes', exact=True).click()
        assert page.locator('[data-section="results-panel"]').get_attribute('aria-current') == 'location'
        assert page.locator('#panel-tab').get_attribute('aria-pressed') == 'true'
        fit_count = page.evaluate('mapCalls.fits.length')
        page.locator('#locate').click()
        expect(page.locator('#center-label')).to_have_text('Curitiba, Paraná, Brasil')
        assert page.evaluate('[center.latitude,center.longitude]') == [-25.43, -49.27]
        assert page.evaluate('mapCalls.fits.length') == fit_count + 1
        page.evaluate("load('synthetic')")
        # Phase 3: data presentation and geographic decisions, all with local synthetic coordinates.
        page.evaluate("""()=>{
          window.originalResults=structuredClone(results);
          results[0].phone='4130000000';results[0].latitude=0;results[0].longitude=0;
          results[0].facebook=results[0].instagram;results[0].website=results[0].instagram;
          results[1].review_rating='';results[1].review_count=0;results[1].latitude=91;results[1].longitude=20;
          results[2].review_rating=5;results[2].review_count=200;results[2].latitude=-25.43;results[2].longitude=-49.27;
          renderResults();
        }""")
        assert page.evaluate('markerIndex.size') == 2
        assert 'map-pin' in page.evaluate("markerIndex.get('0').icon.html")
        assert '4,9' in page.locator('#result-rating').inner_text()
        assert '(41) 3000-0000' in page.locator('#results tr[data-company-id="0"]').inner_text()
        assert page.evaluate("displayPhones('+55 41 999990000')") == '+55 (41) 99999-0000'
        assert page.evaluate("displayPhones('5541999990000')") == '5541999990000'
        assert page.locator('#results tr[data-company-id="0"] a[href="https://instagram.com/example"]').count() == 1
        assert page.locator('#results tr[data-company-id="1"] .rating-cell').inner_text().strip() == '—\n(0)'
        assert page.evaluate('routesUrl(results[1])') is None
        assert page.evaluate('positionOf({latitude:0,longitude:0})') == [0, 0]
        for value in ['', ' ', None, -1, 0, 6, 'invalid', True]:
            assert page.evaluate('value=>ratingValue({review_rating:value})', value) is None
        for value in ['', ' ', None, -1, 1.5, True]:
            assert page.evaluate('value=>reviewCountValue({review_count:value})', value) is None
        page.locator('#sort').select_option('rating')
        assert page.evaluate('visible.map(item=>item.company_id)') == ['2', '0', '1']
        page.locator('#sort').select_option('reviews')
        assert page.evaluate('visible.map(item=>item.company_id)') == ['2', '0', '1']
        page.locator('#fit-results').click()
        assert len(page.evaluate('mapCalls.fits.at(-1).points')) == 2
        assert page.evaluate('mapCalls.fits.at(-1).options.maxZoom') == 15
        page.locator('#filter').fill('Studio Exemplo Centro')
        page.locator('#fit-results').click()
        assert page.evaluate('mapCalls.fits.at(-1).points') == [[0, 0]]
        assert page.locator('#result-companies').inner_text() == '3'  # Campaign KPI is not a filter count.
        page.locator('#filter').fill('')
        page.locator('#sort').select_option('name')
        page.locator('#show-map').click()
        assert page.locator('#map-visible-count').inner_text() == '3 de 3'
        assert page.locator('.map-data-actions button').count() == 4
        assert page.locator('#lead-list .rating').first.evaluate("node=>[...node.children].map(child=>child.textContent)") == ['★ 5,0', '(200)']
        assert page.locator('#lead-list .instagram-link').count() > 0
        assert page.locator('#lead-list .site-link').count() > 0
        assert page.locator('#lead-list .maps-link').count() > 0
        assert page.locator('#lead-list').get_by_text('Rotas', exact=True).count() == 0
        assert page.locator('#results .instagram-link').count() > 0
        assert page.locator('#results .site-link').count() > 0
        assert page.locator('#results .maps-link').count() > 0
        assert page.locator('#map-export').evaluate("node=>getComputedStyle(node).backgroundColor") == 'rgb(219, 234, 255)'
        assert page.evaluate("linkClass('youtube')") == 'link-chip youtube-link'
        assert page.evaluate("linkClass('x')") == 'link-chip x-link'
        assert page.evaluate("linkClass('facebook')") == 'link-chip social-link'
        assert page.evaluate("linkClass('linkedin')") == 'link-chip linkedin-link'
        assert page.evaluate("linkClass('website')") == 'link-chip site-link'
        page.screenshot(path='C:/Users/User/.codex/visualizations/2026/11/02/01a199a0-21c5-7191-8e8b-6d42ad609edb/phase4-final-map-results-desktop.png')
        page.locator('#fit-results').click()
        assert page.evaluate('mapCalls.fits.at(-1).options.paddingTopLeft') == [340, 32]
        page.evaluate("selectCompany('0')")
        expect(page.locator('.lead-card[data-company-id="0"]')).to_have_attribute('aria-current', 'true')
        assert page.evaluate('selected.size') == 0  # Map focus is not manual review.
        expect(page.locator('#clear-map')).to_be_visible()
        page.locator('#clear-map').click()
        expect(page.locator('#map-controls-slot')).to_be_visible()
        expect(page.locator('#stat-companies')).to_have_text('—')
        page.evaluate("load('synthetic').then(()=>panel(false,true))")
        expect(page.locator('#map-leads')).to_be_visible()
        fit_count = page.evaluate('mapCalls.fits.length')
        page.get_by_role('button', name='Painel', exact=True).click()
        page.get_by_role('button', name='Mapa', exact=True).click()
        assert page.evaluate('mapCalls.fits.length') == fit_count
        page.screenshot(path='C:/Users/User/.codex/visualizations/2026/11/02/01a199a0-21c5-7191-8e8b-6d42ad609edb/phase4-final-map-search-desktop.png')
        page.get_by_role('button', name='Painel', exact=True).click()
        rows[0]['latitude'], rows[0]['longitude'] = -25.43, -49.27
        page.evaluate("load('synthetic',true)")
        assert page.evaluate('mapCalls.fits.at(-1).points') == [[-25.43, -49.27]]
        fit_count = page.evaluate('mapCalls.fits.length')
        page.evaluate('refresh()')
        assert page.evaluate('mapCalls.fits.length') == fit_count
        page.evaluate("()=>{results.forEach(row=>{delete row.latitude;delete row.longitude;});renderResults();fitVisibleResults({});}")
        assert page.evaluate('mapCalls.fits.at(-1).points.savedArea') == [-25.43, -49.27]
        expect(page.locator('#fit-results')).to_be_hidden()
        page.evaluate("""()=>{
          results=Array.from({length:30},(_,i)=>({...originalResults[0],company_id:'scroll-'+i,name:'Empresa sintética '+String(i).padStart(2,'0'),latitude:-25.43+i/1000,longitude:-49.27}));
          renderResults();panel(false,true);
        }""")
        assert page.locator('#lead-list').evaluate('node=>node.scrollHeight>node.clientHeight && node.clientHeight>0')
        page.locator('#lead-list').evaluate('node=>node.scrollTop=100')
        page.evaluate('renderResults()')
        assert page.locator('#lead-list').evaluate('node=>node.scrollTop') == 100
        for width, height in [(820, 1000), (390, 844), (375, 667)]:
            page.set_viewport_size({'width': width, 'height': height})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert page.locator('#lead-list').evaluate('node=>node.clientHeight>0')
            if width <= 650:
                assert page.locator('#fit-results').bounding_box()['y'] >= height * .45
        page.set_viewport_size({'width': 1440, 'height': 1050})
        page.get_by_role('button', name='Painel', exact=True).click()
        page.locator('.table-wrap').evaluate('node=>node.scrollTop=50')
        page.evaluate('renderResults()')
        assert page.locator('.table-wrap').evaluate('node=>node.scrollTop') == 50
        page.evaluate("()=>{results=originalResults;renderResults();}")
        del rows[0]['latitude'], rows[0]['longitude']
        page.evaluate("()=>{map=null;markers=null;delete results[0].latitude;delete results[0].longitude;renderResults();}")
        assert page.locator('#results-panel').is_visible()
        page.set_viewport_size({'width': 820, 'height': 1000})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert len(page.locator('.results-metrics').evaluate("node=>getComputedStyle(node).gridTemplateColumns.split(' ')")) == 3
        assert page.locator('.map-card').bounding_box()['y'] > page.locator('.table-wrap').bounding_box()['y']
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), page.evaluate("[...document.querySelectorAll('body *')].filter(n=>n.getBoundingClientRect().right>innerWidth).map(n=>[n.tagName,n.className,n.id,Math.round(n.getBoundingClientRect().right)])")
        page.locator('#manual-review summary').click()
        page.locator('#results input').first.check()
        expect(page.locator('#review-tools')).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.get_by_role('button', name='Mapa', exact=True).click()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.get_by_role('button', name='Painel', exact=True).click()
        assert page.locator('#results input:checked').count() == 1
        page.locator('#results input').first.uncheck()
        page.locator('#manual-review summary').click()
        page.set_viewport_size({'width': 1440, 'height': 900})
        page.evaluate("goToSection('results-panel');document.querySelector('#results-panel').scrollIntoView({behavior:'instant',block:'start'})")
        assert page.locator('.map-card').bounding_box()['x'] > page.locator('.table-wrap').bounding_box()['x']
        output = Path('C:/Users/User/.codex/visualizations/2026/11/02/01a199a0-21c5-7191-8e8b-6d42ad609edb/phase3-results-desktop.png')
        page.screenshot(path=str(output))
        page.evaluate("goToSection('overview');window.scrollTo({top:0,behavior:'instant'})")
        page.screenshot(path='C:/Users/User/.codex/visualizations/2026/11/02/01a199a0-21c5-7191-8e8b-6d42ad609edb/phase4-final-overview-desktop.png')
        page.set_viewport_size({'width': 390, 'height': 844})
        page.evaluate("goToSection('results-panel');document.querySelector('#results-panel').scrollIntoView({behavior:'instant',block:'start'})")
        page.screenshot(path='C:/Users/User/.codex/visualizations/2026/11/02/01a199a0-21c5-7191-8e8b-6d42ad609edb/phase4-final-results-mobile.png', full_page=True)
        page.evaluate("load('synthetic')")
        delete_count = len([item for item in mutations if item[0].startswith('/api/v1/campaigns/') and item[1] is None])
        page.once('dialog', lambda dialog: dialog.dismiss())
        page.get_by_role('button', name='Excluir Outra busca sintética').click()
        assert len([item for item in mutations if item[0].startswith('/api/v1/campaigns/') and item[1] is None]) == delete_count
        delete_state['mode'] = 'missing'
        page.once('dialog', lambda dialog: dialog.accept())
        page.get_by_role('button', name='Excluir Outra busca sintética').click()
        expect(page.locator('#result-feedback')).to_contain_text('não foi encontrada')
        delete_state['mode'] = 'active'
        page.once('dialog', lambda dialog: dialog.accept())
        page.get_by_role('button', name='Excluir Outra busca sintética').click()
        expect(page.locator('#result-feedback')).to_contain_text('execução ativa')
        delete_state['mode'] = 'hold'
        page.once('dialog', lambda dialog: dialog.accept())
        page.get_by_role('button', name='Excluir Outra busca sintética').click()
        page.wait_for_function('mutating')
        page.evaluate("deleteCampaign({campaign_id:'other',name:'Outra busca sintética'})")
        assert len(delete_state['held']) == 1
        delete_state['held'].pop().fulfill(status=409, body=json.dumps({'error': 'campaign has active jobs'}), content_type='application/json')
        expect(page.locator('#result-feedback')).to_contain_text('execução ativa')
        delete_state['mode'] = 'success'
        page.once('dialog', lambda dialog: dialog.accept())
        page.get_by_role('button', name='Excluir Estúdios de pilates · Curitiba').click()
        expect(page.locator('#result-feedback')).to_contain_text('excluída')
        assert page.evaluate('active') is None
        assert page.locator('#results tr').count() == 0
        assert page.evaluate('markerIndex.size') == 0
        assert page.locator('#stat-searches').inner_text() == '1'
        assert page.locator('#stat-companies').inner_text() == '—'
        assert page.locator('#result-companies').inner_text() == '—'
        assert page.locator('.history-item').count() == 1
        assert not errors, errors
        print(f'PASS: navigation/review/export regressions; Phase 3 metrics, missing values, URL deduplication, rating/review sort, valid coordinate bounds, fit/filter/focus/viewport, independent scrolling, desktop/tablet/mobile; zero JS errors. External requests blocked: {len(blocked)}. Screenshot: {output}')
        browser.close()


if __name__ == '__main__':
    run()
