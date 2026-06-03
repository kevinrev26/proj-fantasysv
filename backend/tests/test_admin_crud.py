import pytest
import time
from fastapi.testclient import TestClient
from app.main import app
from app import models, security
from app.database import SessionLocal

client = TestClient(app)

@pytest.fixture(scope="module")
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

@pytest.fixture(scope="module")
def admin_token(db):
    # Find or create a test admin user
    admin = db.query(models.User).filter(models.User.email == "test_admin_crud@example.com").first()
    if not admin:
        admin = models.User(
            username="testadmincrud",
            email="test_admin_crud@example.com",
            hashed_password=security.hash_password("adminpass"),
            role=models.UserRole.admin,
            is_active=True,
            onboarding_complete=True
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
    
    token = security.create_access_token_for_user(admin)
    return token

def test_tournament_phase_crud(admin_token, db):
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    # 1. Create a season to associate with the phase
    season = models.Season(
        name="Test Season CRUD Phase",
        start_date="2026-08-01",
        end_date="2027-06-01"
    )
    db.add(season)
    db.commit()
    db.refresh(season)
    
    try:
        # Create Phase
        res = client.post(
            f"/admin/seasons/{season.id}/phases",
            json={"name": "quarterfinal"},
            headers=headers
        )
        assert res.status_code == 201
        phase_data = res.json()
        assert phase_data["name"] == "quarterfinal"
        phase_id = phase_data["id"]
        
        # Read Phases
        res = client.get(f"/admin/phases?season_id={season.id}", headers=headers)
        assert res.status_code == 200
        phases = res.json()
        assert len(phases) == 1
        assert phases[0]["id"] == phase_id
        
        # Update Phase
        res = client.patch(
            f"/admin/phases/{phase_id}",
            json={"name": "semifinal"},
            headers=headers
        )
        assert res.status_code == 200
        assert res.json()["name"] == "semifinal"
        
        # Delete Phase
        res = client.delete(f"/admin/phases/{phase_id}", headers=headers)
        assert res.status_code == 200
        
        # Verify deletion
        res = client.get(f"/admin/phases?season_id={season.id}", headers=headers)
        assert res.status_code == 200
        assert len(res.json()) == 0
        
    finally:
        # Clean up season
        db.delete(season)
        db.commit()

def test_team_and_player_crud(admin_token, db):
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    # Setup: Season, League, Team, Phase, Player
    season = models.Season(
        name="Test Season CRUD Team",
        start_date="2026-08-01",
        end_date="2027-06-01"
    )
    db.add(season)
    db.commit()
    
    league = models.League(name="Test League CRUD", season_id=season.id)
    db.add(league)
    db.commit()
    
    phase = models.TournamentPhase(name=models.PhaseName.group, season_id=season.id)
    db.add(phase)
    db.commit()
    
    db.refresh(season)
    db.refresh(league)
    db.refresh(phase)
    
    try:
        # 1. Create Team
        res = client.post(
            f"/admin/leagues/{league.id}/teams",
            json={"name": "Test Team CRUD"},
            headers=headers
        )
        assert res.status_code == 201
        team_data = res.json()
        assert team_data["name"] == "Test Team CRUD"
        team_id = team_data["id"]
        
        # Read Teams
        res = client.get(f"/admin/teams?league_id={league.id}", headers=headers)
        assert res.status_code == 200
        teams = res.json()
        assert len(teams) == 1
        assert teams[0]["id"] == team_id
        
        # 2. Create Player
        res = client.post(
            f"/admin/teams/{team_id}/players",
            json={
                "name": "Test Player CRUD",
                "position": "FW",
                "tier": "A",
                "credit_value": 10
            },
            headers=headers
        )
        assert res.status_code == 201
        player_data = res.json()
        assert player_data["name"] == "Test Player CRUD"
        assert player_data["is_active"] is True
        player_id = player_data["id"]
        
        # Read Players
        res = client.get(f"/admin/players?team_id={team_id}", headers=headers)
        assert res.status_code == 200
        players = res.json()
        assert len(players) == 1
        assert players[0]["id"] == player_id
        
        # Update Player (name, position, credits)
        res = client.patch(
            f"/admin/players/{player_id}",
            json={
                "name": "Updated Player Name",
                "position": "MF",
                "credit_value": 12
            },
            headers=headers
        )
        assert res.status_code == 200
        updated_p = res.json()
        assert updated_p["name"] == "Updated Player Name"
        assert updated_p["position"] == "MF"
        assert updated_p["credit_value"] == 12
        
        # 3. Test Elimination Logic
        res = client.patch(
            f"/admin/teams/{team_id}",
            json={"eliminated_in_phase_id": phase.id},
            headers=headers
        )
        assert res.status_code == 200
        assert res.json()["eliminated_in_phase_id"] == phase.id
        
        # Wait up to 10 seconds for Celery task processing to complete
        p_obj = None
        for _ in range(20):
            db.expire_all()
            p_obj = db.query(models.Player).filter(models.Player.id == player_id).first()
            if p_obj and not p_obj.is_active:
                break
            time.sleep(0.5)
        
        # Assert player is deactivated
        assert p_obj is not None
        assert p_obj.is_active is False
        
        # 4. Test Restoration Logic (Reactivation)
        res = client.patch(
            f"/admin/teams/{team_id}",
            json={"eliminated_in_phase_id": None},
            headers=headers
        )
        assert res.status_code == 200
        assert res.json()["eliminated_in_phase_id"] is None
        
        # Wait up to 10 seconds for Celery reactivation
        for _ in range(20):
            db.expire_all()
            p_obj = db.query(models.Player).filter(models.Player.id == player_id).first()
            if p_obj and p_obj.is_active:
                break
            time.sleep(0.5)
            
        assert p_obj is not None
        assert p_obj.is_active is True
        
        # 5. Delete Player
        res = client.delete(f"/admin/players/{player_id}", headers=headers)
        assert res.status_code == 200
        
        # Verify Player is deleted
        res = client.get(f"/admin/players?team_id={team_id}", headers=headers)
        assert res.status_code == 200
        assert len(res.json()) == 0
        
        # 6. Delete Team
        res = client.delete(f"/admin/teams/{team_id}", headers=headers)
        assert res.status_code == 200
        
        # Verify Team is deleted
        res = client.get(f"/admin/teams?league_id={league.id}", headers=headers)
        assert res.status_code == 200
        assert len(res.json()) == 0
        
    finally:
        # Clean up database resources
        db.delete(phase)
        db.delete(league)
        db.delete(season)
        db.commit()
