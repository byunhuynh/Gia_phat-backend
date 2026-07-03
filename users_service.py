# users_service.py
from models import User
from passlib.context import CryptContext


pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

ROLE_ORDER = ["sales", "supervisor", "regional_director", "director", "admin"]



def role_index(role):
    return ROLE_ORDER.index(role) if role in ROLE_ORDER else -1
    

def can_create(current_role, target_role):
    return role_index(target_role) < role_index(current_role)

def hash_password(password):
    return pwd_context.hash(password)




def resolve_manager(current, db, target_role, manager_id):
    """
    Logic mới: Hỗ trợ nhảy bậc. 
    Manager có thể là bất kỳ ai có Role cao hơn Role đang tạo.
    """
    if not manager_id:
        return current # Mặc định là người đang tạo

    manager = db.query(User).get(manager_id)
    if not manager:
        return None

    # Manager phải có cấp bậc cao hơn người được tạo
    if role_index(manager.role) > role_index(target_role):
        return manager
    
    return None