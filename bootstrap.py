"""
Bootstrap script to create agents directory and move files
"""
import os
import shutil

# Create agents directory
agents_dir = "services/FYPilot.AI/app/agents"
os.makedirs(agents_dir, exist_ok=True)
print(f"Created directory: {agents_dir}")

# Create __init__.py
init_file = os.path.join(agents_dir, "__init__.py")
init_content = """'''
AI Agents module for FYPilot.
'''
from app.agents.project_idea_agent import ProjectIdeaAgent

__all__ = ["ProjectIdeaAgent"]
"""
with open(init_file, 'w') as f:
    f.write(init_content)
print(f"Created: {init_file}")

# Move project_idea_agent.py from services to agents
src = "services/FYPilot.AI/app/services/project_idea_agent.py"
dst = os.path.join(agents_dir, "project_idea_agent.py")
if os.path.exists(src):
    shutil.move(src, dst)
    print(f"Moved {src} to {dst}")
else:
    print(f"Warning: {src} not found")

print("Bootstrap complete!")

