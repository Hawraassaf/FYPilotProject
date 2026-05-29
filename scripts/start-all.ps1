Write-Host ""
Write-Host "=======================================" -ForegroundColor Cyan
Write-Host "  Services starting in separate windows" -ForegroundColor Cyan
Write-Host "=======================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Python AI  :  http://localhost:8000/health" -ForegroundColor Green
Write-Host "Web app    :  http://localhost:5000" -ForegroundColor Green
Write-Host "System test:  http://localhost:5000/SystemTest" -ForegroundColor Green
Write-Host ""
Write-Host "Optional - REST API (Swagger):" -ForegroundColor Gray
Write-Host "  cd src\FYPilot.Api" -ForegroundColor Gray
Write-Host "  dotnet run --urls http://localhost:8080" -ForegroundColor Gray
Write-Host "  http://localhost:8080/swagger" -ForegroundColor Gray
Write-Host ""
Write-Host "Demo accounts password is password123:" -ForegroundColor Gray
Write-Host "  student@fyp.com" -ForegroundColor Gray
Write-Host "  supervisor@fyp.com" -ForegroundColor Gray
Write-Host "  admin@fyp.com" -ForegroundColor Gray
Write-Host ""