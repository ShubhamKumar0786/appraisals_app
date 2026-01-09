import os
import re
import json
import time
import requests
from datetime import datetime
from flask import Flask, render_template, request, jsonify, Response
from flask_cors import CORS
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
import threading

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)

# ============================================
# CONFIGURATION
# ============================================
CONFIG = {
    'SIGNAL_EMAIL': os.getenv('SIGNAL_EMAIL', ''),
    'SIGNAL_PASSWORD': os.getenv('SIGNAL_PASSWORD', ''),
    'SUPABASE_URL': os.getenv('SUPABASE_URL', ''),
    'SUPABASE_API_KEY': os.getenv('SUPABASE_API_KEY', ''),
    'SUPABASE_TABLE': os.getenv('SUPABASE_TABLE', 'inventory'),
}

# Global state for processing
processing_state = {
    'is_processing': False,
    'current_vin': '',
    'progress': 0,
    'total': 0,
    'results': [],
    'logs': []
}

# ============================================
# HELPER FUNCTIONS
# ============================================

def log_message(msg):
    """Add log message"""
    timestamp = datetime.now().strftime('%H:%M:%S')
    processing_state['logs'].append(f"[{timestamp}] {msg}")
    print(f"[{timestamp}] {msg}")

def get_supabase_headers(api_key):
    return {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

def parse_price(price_str):
    if not price_str:
        return 0.0
    try:
        cleaned = re.sub(r'[^\d.-]', '', str(price_str))
        return float(cleaned) if cleaned else 0.0
    except:
        return 0.0

def is_valid_vin(vin, prefixes=('1', '4', '5')):
    if not vin or len(vin) < 17:
        return False
    return vin.strip().upper().startswith(prefixes)

def fetch_inventory(supabase_url, api_key, table_name):
    """Fetch all inventory from Supabase"""
    all_data = []
    batch_size = 1000
    offset = 0
    
    try:
        while True:
            url = f"{supabase_url}/rest/v1/{table_name}?select=*&limit={batch_size}&offset={offset}"
            response = requests.get(url, headers=get_supabase_headers(api_key), timeout=60)
            response.raise_for_status()
            batch = response.json()
            
            if not batch:
                break
            all_data.extend(batch)
            if len(batch) < batch_size:
                break
            offset += batch_size
        
        return all_data
    except Exception as e:
        log_message(f"❌ Error fetching inventory: {e}")
        return []

def save_to_appraisal_results(supabase_url, api_key, result):
    """Save result to appraisal_results table"""
    try:
        url = f"{supabase_url}/rest/v1/appraisal_results"
        
        export_val = None
        if result.get('export_value_cad'):
            try:
                clean_val = str(result['export_value_cad']).replace(',', '').replace('$', '').strip()
                export_val = float(clean_val)
            except:
                pass
        
        price_val = result.get('list_price', 0)
        if isinstance(price_val, str):
            price_val = parse_price(price_val)
        
        profit_val = result.get('profit')
        if profit_val is not None:
            try:
                profit_val = float(profit_val)
            except:
                profit_val = None
        
        payload = {
            "vin": result.get('vin', ''),
            "kilometers": str(result.get('odometer', '')),
            "listing_link": result.get('listing_url', ''),
            "carfax_link": result.get('carfax_link', ''),
            "make": result.get('make', ''),
            "model": result.get('model', ''),
            "trim": result.get('signal_trim', ''),
            "price": price_val,
            "export_value": export_val,
            "profit": profit_val,
            "status": result.get('status', '')
        }
        
        response = requests.post(url, json=payload, headers=get_supabase_headers(api_key), timeout=30)
        
        if response.status_code in [200, 201]:
            log_message(f"✅ Saved to DB: {result.get('vin')}")
            return True
        else:
            log_message(f"❌ Save failed: {response.status_code}")
            return False
    except Exception as e:
        log_message(f"❌ Save error: {e}")
        return False

# ============================================
# SIGNAL.VIN AUTOMATION CLASS
# ============================================

class SignalVinAutomation:
    def __init__(self, headless=True):
        self.headless = headless
        self.browser = None
        self.page = None
        self.playwright = None
        self.logged_in = False
        self.signal_url = "https://app.signal.vin"
        self.captured_responses = []
        self.vehicle_make = ''
        self.vehicle_model = ''
        self.vehicle_trim = ''
    
    def start(self):
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=self.headless, slow_mo=50)
        context = self.browser.new_context(
            viewport={'width': 1280, 'height': 720},
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        )
        self.page = context.new_page()
        self.page.on("response", self._capture_response)
        return True
    
    def _capture_response(self, response):
        try:
            url = response.url
            if 'signal.vin' in url or 'export' in url.lower():
                try:
                    body = response.text()
                    self.captured_responses.append({
                        'url': url,
                        'status': response.status,
                        'body': body if body else ''
                    })
                except:
                    pass
        except:
            pass
    
    def stop(self):
        try:
            if self.browser:
                self.browser.close()
        except:
            pass
        try:
            if self.playwright:
                self.playwright.stop()
        except:
            pass
    
    def login(self, email, password):
        try:
            log_message("🔐 Logging in to Signal.vin...")
            self.page.goto(self.signal_url, wait_until='networkidle')
            time.sleep(3)
            
            if "dashboard" in self.page.url or "appraisal" in self.page.url:
                log_message("✅ Already logged in!")
                self.logged_in = True
                return True
            
            try:
                login_btn = self.page.locator('a:has-text("Login"), button:has-text("Login")').first
                if login_btn.is_visible():
                    login_btn.click()
                    time.sleep(3)
            except:
                pass
            
            time.sleep(2)
            
            email_field = self.page.locator('input').nth(0)
            email_field.click()
            time.sleep(0.3)
            email_field.fill('')
            email_field.type(email, delay=50)
            
            self.page.keyboard.press('Tab')
            time.sleep(0.3)
            self.page.keyboard.type(password, delay=50)
            
            # Try to click agree checkbox
            try:
                checkbox = self.page.locator('input[type="checkbox"]').first
                if checkbox.is_visible(timeout=2000):
                    checkbox.click()
            except:
                pass
            
            # Click login button
            try:
                login_submit = self.page.locator('button:has-text("Login"), button:has-text("Sign in")').first
                if login_submit.is_visible(timeout=2000):
                    login_submit.click()
            except:
                self.page.keyboard.press('Enter')
            
            time.sleep(5)
            
            # Wait for redirect
            for _ in range(20):
                if "dashboard" in self.page.url or "appraisal" in self.page.url:
                    log_message("✅ Login successful!")
                    self.logged_in = True
                    return True
                time.sleep(1)
            
            log_message("❌ Login failed - check credentials")
            return False
            
        except Exception as e:
            log_message(f"❌ Login error: {e}")
            return False
    
    def extract_export_value(self):
        """Extract export value from API responses"""
        # Wait for Flutter
        time.sleep(3)
        
        exchange_rate = None
        fx_cushion = 0
        export_cost = None
        target_gpu = None
        us_wholesale_value = None
        customs_duty_rate = 0
        weekly_depreciation_factor = 0
        average_days_in_inventory = 0
        self.vehicle_make = ''
        self.vehicle_model = ''
        self.vehicle_trim = ''
        
        for resp in self.captured_responses:
            url = resp.get('url', '')
            body = resp.get('body', '')
            
            if not body or body.startswith('(function') or body.startswith('<!'):
                continue
            
            try:
                data = json.loads(body)
            except:
                continue
            
            # Decode API
            if 'decode' in url and 'signal.vin' in url:
                if 'make' in data:
                    self.vehicle_make = data.get('make', '')
                if 'model' in data:
                    self.vehicle_model = data.get('model', '')
                if 'selected_trim' in data and data['selected_trim']:
                    self.vehicle_trim = data.get('selected_trim', '')
                elif 'suggested_trim' in data and data['suggested_trim']:
                    self.vehicle_trim = data.get('suggested_trim', '')
                
                if 'customs_duty_rate' in data and data['customs_duty_rate'] is not None:
                    try:
                        customs_duty_rate = float(data['customs_duty_rate'])
                    except:
                        pass
            
            # Offer/initial API
            if 'offer/initial' in url:
                if 'exchange_rate' in data:
                    er = data['exchange_rate']
                    if isinstance(er, dict) and 'to_currency_rate' in er:
                        exchange_rate = float(er['to_currency_rate'])
                    elif isinstance(er, (int, float)):
                        exchange_rate = float(er)
                
                if 'current_weekly_depreciation_factor' in data:
                    weekly_depreciation_factor = float(data['current_weekly_depreciation_factor'])
                
                if 'offer_setup' in data:
                    setup = data['offer_setup']
                    if 'export_cost_amount' in setup:
                        export_cost = float(setup['export_cost_amount'])
                    if 'target_gpu_amount' in setup:
                        target_gpu = float(setup['target_gpu_amount'])
                    if 'fx_cushion_amount' in setup:
                        fx_cushion = float(setup['fx_cushion_amount'])
                    if 'average_days_in_inventory' in setup:
                        average_days_in_inventory = int(setup['average_days_in_inventory'])
            
            # Wholesale value trends API
            if 'wholesale_value_trends' in url:
                if 'wholesale_value_trends' in data and data['wholesale_value_trends'] is not None:
                    trends_data = data['wholesale_value_trends']
                    
                    if 'predicted_wholesale_value' in trends_data and trends_data['predicted_wholesale_value'] is not None:
                        pwv = trends_data['predicted_wholesale_value']
                        if isinstance(pwv, dict) and 'amount' in pwv:
                            us_wholesale_value = float(pwv['amount'])
                        elif isinstance(pwv, (int, float)):
                            us_wholesale_value = float(pwv)
        
        # Calculate export value
        if us_wholesale_value and exchange_rate:
            effective_fx = exchange_rate - fx_cushion
            customs_duty = us_wholesale_value * customs_duty_rate
            
            weeks = average_days_in_inventory / 7 if average_days_in_inventory > 0 else 0
            depreciation_rate = weekly_depreciation_factor / 100 if weekly_depreciation_factor > 0 else 0
            depreciation_usd = us_wholesale_value * depreciation_rate * weeks
            
            net_usd = us_wholesale_value - (export_cost or 0) - (target_gpu or 0) - customs_duty - depreciation_usd
            export_value_cad = int(round(net_usd * effective_fx))
            
            log_message(f"💰 Calculated: ${us_wholesale_value} USD → ${export_value_cad} CAD")
            return str(export_value_cad)
        
        return None
    
    def appraise_vehicle(self, vin, odometer, trim=None, list_price=0, listing_url='', carfax_link='', make='', model=''):
        result = {
            'vin': vin,
            'odometer': odometer,
            'trim': trim,
            'list_price': list_price,
            'listing_url': listing_url,
            'carfax_link': carfax_link,
            'make': make,
            'model': model,
            'signal_trim': '',
            'export_value_cad': None,
            'profit': None,
            'status': 'PENDING',
            'error': None
        }
        
        try:
            self.captured_responses = []
            
            url = f"{self.signal_url}/appraisal/calculate-export?vin={vin}&odometer={odometer}&is-km=true"
            log_message(f"🌐 Processing: {vin}")
            
            self.page.goto(url)
            time.sleep(12)
            
            if 'login' in self.page.url.lower():
                result['status'] = 'SESSION_EXPIRED'
                result['error'] = 'Need to re-login'
                return result
            
            # Scroll to load content
            for _ in range(3):
                self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(1)
            
            time.sleep(5)
            
            export_value = self.extract_export_value()
            
            if export_value:
                result['export_value_cad'] = export_value
                export_num = float(export_value)
                if export_num > 0 and list_price > 0:
                    result['profit'] = export_num - list_price
                    result['status'] = 'PROFIT' if result['profit'] > 0 else 'LOSS'
                else:
                    result['status'] = 'SUCCESS'
            else:
                result['status'] = 'NO DATA'
                result['error'] = 'Could not extract export value'
            
            result['signal_trim'] = self.vehicle_trim
            
        except Exception as e:
            result['error'] = str(e)
            result['status'] = 'ERROR'
            log_message(f"❌ Error: {e}")
        
        return result

# ============================================
# BACKGROUND PROCESSING
# ============================================

def process_vehicles_background(vehicles, config):
    """Process vehicles in background thread"""
    global processing_state
    
    processing_state['is_processing'] = True
    processing_state['total'] = len(vehicles)
    processing_state['progress'] = 0
    processing_state['results'] = []
    processing_state['logs'] = []
    
    automation = SignalVinAutomation(headless=True)
    
    try:
        log_message("🚀 Starting browser...")
        automation.start()
        
        if not automation.login(config['SIGNAL_EMAIL'], config['SIGNAL_PASSWORD']):
            log_message("❌ Login failed!")
            processing_state['is_processing'] = False
            return
        
        for i, item in enumerate(vehicles):
            processing_state['current_vin'] = item['vin']
            processing_state['progress'] = i + 1
            
            result = automation.appraise_vehicle(
                item['vin'],
                item['odometer'],
                item.get('trim', ''),
                item.get('list_price', 0),
                item.get('listing_url', ''),
                item.get('carfax_link', ''),
                item.get('make', ''),
                item.get('model', '')
            )
            
            processing_state['results'].append(result)
            
            # Save to database if export value found
            if result.get('export_value_cad'):
                save_to_appraisal_results(
                    config['SUPABASE_URL'],
                    config['SUPABASE_API_KEY'],
                    result
                )
            
            time.sleep(1)
        
        log_message(f"✅ Completed processing {len(vehicles)} vehicles!")
        
    except Exception as e:
        log_message(f"❌ Error: {e}")
    finally:
        automation.stop()
        processing_state['is_processing'] = False

# ============================================
# ROUTES
# ============================================

@app.route('/')
def index():
    return render_template('index.html', config=CONFIG)

@app.route('/api/config', methods=['POST'])
def update_config():
    """Update configuration"""
    data = request.json
    CONFIG['SIGNAL_EMAIL'] = data.get('signal_email', CONFIG['SIGNAL_EMAIL'])
    CONFIG['SIGNAL_PASSWORD'] = data.get('signal_password', CONFIG['SIGNAL_PASSWORD'])
    CONFIG['SUPABASE_URL'] = data.get('supabase_url', CONFIG['SUPABASE_URL'])
    CONFIG['SUPABASE_API_KEY'] = data.get('supabase_api_key', CONFIG['SUPABASE_API_KEY'])
    CONFIG['SUPABASE_TABLE'] = data.get('supabase_table', CONFIG['SUPABASE_TABLE'])
    return jsonify({'status': 'success'})

@app.route('/api/fetch-inventory')
def api_fetch_inventory():
    """Fetch inventory from Supabase"""
    data = fetch_inventory(CONFIG['SUPABASE_URL'], CONFIG['SUPABASE_API_KEY'], CONFIG['SUPABASE_TABLE'])
    
    # Filter valid VINs
    valid = []
    for row in data:
        vin = str(row.get('vin', '')).strip().upper()
        if is_valid_vin(vin):
            valid.append({
                'vin': vin,
                'odometer': str(row.get('kilometers', '0')).strip() or '0',
                'trim': str(row.get('trim', '')).strip(),
                'list_price': parse_price(str(row.get('price', ''))),
                'listing_url': str(row.get('listing_link', '')).strip(),
                'carfax_link': str(row.get('carfax_link', '')).strip(),
                'make': str(row.get('make', '')).strip(),
                'model': str(row.get('model', '')).strip()
            })
    
    return jsonify({
        'total': len(data),
        'valid': len(valid),
        'vehicles': valid
    })

@app.route('/api/start-processing', methods=['POST'])
def api_start_processing():
    """Start processing vehicles"""
    global processing_state
    
    if processing_state['is_processing']:
        return jsonify({'status': 'error', 'message': 'Already processing'})
    
    data = request.json
    vehicles = data.get('vehicles', [])
    
    if not vehicles:
        return jsonify({'status': 'error', 'message': 'No vehicles to process'})
    
    # Start background thread
    thread = threading.Thread(target=process_vehicles_background, args=(vehicles, CONFIG.copy()))
    thread.daemon = True
    thread.start()
    
    return jsonify({'status': 'success', 'message': f'Started processing {len(vehicles)} vehicles'})

@app.route('/api/status')
def api_status():
    """Get processing status"""
    return jsonify({
        'is_processing': processing_state['is_processing'],
        'current_vin': processing_state['current_vin'],
        'progress': processing_state['progress'],
        'total': processing_state['total'],
        'results_count': len(processing_state['results']),
        'logs': processing_state['logs'][-20:]  # Last 20 logs
    })

@app.route('/api/results')
def api_results():
    """Get processing results"""
    results = processing_state['results']
    
    profitable = [r for r in results if r.get('profit') and r['profit'] > 0]
    losses = [r for r in results if r.get('profit') is not None and r['profit'] <= 0]
    errors = [r for r in results if r.get('status') in ['ERROR', 'NO DATA']]
    
    total_profit = sum(r['profit'] for r in profitable)
    
    return jsonify({
        'all': results,
        'profitable': profitable,
        'losses': losses,
        'errors': errors,
        'total_profit': total_profit,
        'summary': {
            'total': len(results),
            'profitable': len(profitable),
            'losses': len(losses),
            'errors': len(errors)
        }
    })

@app.route('/api/stop-processing', methods=['POST'])
def api_stop_processing():
    """Stop processing"""
    global processing_state
    processing_state['is_processing'] = False
    return jsonify({'status': 'success'})

# ============================================
# MAIN
# ============================================

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
