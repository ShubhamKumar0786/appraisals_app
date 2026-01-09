# 🚗 Signal.vin Bulk Appraisal - Flask Version

## ⚡ Why Flask?
- **Faster startup** - No heavy Streamlit overhead
- **Lighter** - ~50MB vs Streamlit ~200MB
- **No session state issues** - Simple REST API
- **Better for production**

## 📁 Files
```
flask_deploy/
├── app.py              # Flask backend + Playwright automation
├── templates/
│   └── index.html      # Frontend UI (Bootstrap)
├── requirements.txt    # Python dependencies
├── Dockerfile          # Docker config
├── render.yaml         # Render config
├── .env                # Your secrets
└── .gitignore
```

## 🚀 Local Run
```bash
pip install -r requirements.txt
playwright install chromium
python app.py
```
Open: http://localhost:5000

## 🌐 Deploy to Render

### Step 1: Push to GitHub
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USER/signal-vin-flask.git
git push -u origin main
```

### Step 2: Deploy on Render
1. Go to https://render.com
2. New → Web Service → Connect GitHub repo
3. Add Environment Variables:
   - `SIGNAL_EMAIL`
   - `SIGNAL_PASSWORD`
   - `SUPABASE_URL`
   - `SUPABASE_API_KEY`
4. Deploy!

## 🎯 Features
- ✅ Fetch inventory from Supabase
- ✅ Process VINs via Signal.vin API interception
- ✅ Calculate export values
- ✅ Save results to appraisal_results table
- ✅ Real-time progress updates
- ✅ Profitable vehicles filter

## 📞 Support
Built for Bikram @ DreamFleet
