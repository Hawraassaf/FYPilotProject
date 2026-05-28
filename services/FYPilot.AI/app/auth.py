import os
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError

JWT_SECRET = os.getenv("SESSION_SECRET", "fallback_dev_secret_change_in_production")
JWT_ALGORITHM = "HS256"

security = HTTPBearer()


def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)) -> dict:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM],
                             options={"verify_aud": False, "verify_iss": False})
        user_id = payload.get("userId") or payload.get("sub")
        user_role = payload.get("userRole") or payload.get("role")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        return {"user_id": int(user_id), "user_role": user_role}
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Token error: {str(e)}")
