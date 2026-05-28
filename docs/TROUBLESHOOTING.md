# Troubleshooting — FYPilot

---

## Database Issues

### "Connection refused" or "Failed to connect to localhost:5432"

**Cause:** PostgreSQL is not running.

**Fix:**
- Windows: Open Services → find `postgresql-x64-XX` → Start
- Or open pgAdmin — it will start the service automatically
- Check that port 5432 is not blocked by a firewall

---

### "Database fyp_db does not exist"

**Fix:** Create it in psql or pgAdmin:
```sql
CREATE DATABASE fyp_db;
```

---

### "Password authentication failed for user postgres"

**Cause:** The password doesn't match the configured value.

**Fix:**
- Check your `PGPASSWORD` environment variable is set to `123456`
- Or reset the postgres password in psql:
  ```sql
  ALTER USER postgres PASSWORD '123456';
  ```

---

### Tables don't exist / "relation does not exist"

**Cause:** `EnsureCreated()` failed on startup.

**Fix:**
1. Check the app startup logs for a database error
2. Ensure PostgreSQL is running and `fyp_db` exists
3. Restart the app — it calls `EnsureCreated()` on every start

---

## Python AI Service Issues

### AI features show "AI service unavailable" or return empty results

**Cause:** The Python service is not running.

**Fix:**
```bash
cd services/FYPilot.AI
.venv\Scripts\activate     # Windows
python run.py
```
Verify: http://localhost:8000/health should return `{"status": "Python AI service running"}`

---

### "ModuleNotFoundError" when starting the Python service

**Cause:** Dependencies not installed in the virtual environment.

**Fix:**
```bash
cd services/FYPilot.AI
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

---

### Python service starts but AI endpoints return 500 errors

**Cause:** Heavy ML packages (scikit-learn, numpy, pandas) may have installation issues.

**Fix:** The service has lightweight fallbacks — basic endpoints still work.
Check the Python terminal for error messages and reinstall:
```bash
pip install numpy pandas scikit-learn --upgrade
```

---

### Port 8000 already in use

**Fix:**
```bash
# Find and kill the process on port 8000 (Windows)
netstat -ano | findstr :8000
taskkill /PID <pid> /F
```

---

## .NET / Visual Studio Issues

### Build errors after opening the solution

**Fix:**
1. Right-click the solution → **Restore NuGet Packages**
2. Build → **Clean Solution** then **Build Solution**
3. Ensure .NET 8 SDK is installed: `dotnet --version`

---

### App starts but browser shows blank page or "This site can't be reached"

**Cause:** App started on a different port than expected.

**Fix:** Check the terminal output — it shows the actual URL. Default is http://localhost:5000

---

### "No authenticationScheme was specified"

**Cause:** A Razor Page requires authentication but the auth middleware isn't set up.

**Fix:** This shouldn't happen — `Program.cs` already registers Cookie auth. If you see this, ensure `app.UseAuthentication()` and `app.UseAuthorization()` are in the right order in `Program.cs`.

---

### Environment variables not being picked up

**Fix:**
- Restart Visual Studio after setting system environment variables
- Or add them to `Properties/launchSettings.json` under the `FYPilot.Web` profile:
  ```json
  "environmentVariables": {
    "PGHOST": "localhost",
    "PGPORT": "5432",
    "PGDATABASE": "fyp_db",
    "PGUSER": "postgres",
    "PGPASSWORD": "123456",
    "AI_SERVICE_URL": "http://localhost:8000"
  }
  ```

---

## System Test Page

Navigate to **http://localhost:5000/SystemTest** and click **Run Tests** to diagnose all service connections at once. Each card shows a green/red status with a specific error message to help pinpoint the problem.

---

## Demo Account Login Issues

If demo accounts don't exist:
1. The database may have been created without seeding
2. Drop and recreate the database, then restart the app
3. `DataSeeder.cs` runs automatically and creates the accounts

| Role | Email | Password |
|------|-------|----------|
| Student | student@fyp.com | password123 |
| Supervisor | supervisor@fyp.com | password123 |
| Admin | admin@fyp.com | password123 |
