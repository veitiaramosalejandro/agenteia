DATASETS = {
    "resources": """
        SELECT r.DisplayName, r.ResourceId, r.ActiveIDLogin2Resource,
               a.IDAgentResource, l.FullName
        FROM dbo.SysResources r
        INNER JOIN dbo.SysLogin l
          ON l.ActiveIDLogin2Resource = r.ActiveIDLogin2Resource
        LEFT JOIN dbo.SysResource2Agent a
          ON a.IDHumanResource = r.ResourceId AND a.Active = 1
    """,
    "logins": """
        SELECT Username, FullName, Password, Salt, IDLogin,
               LastIDResource, ActiveIDLogin2Resource
        FROM dbo.SysLogin
    """,
    "workrooms": """
        SELECT Code, Name, Description, IDWorkRoom
        FROM dbo.SysWorkRoom
    """,
    "workroom-resources": """
        SELECT r.DisplayName, r.ResourceId, l.FullName,
               w.Code, w.Name, w.IDWorkRoom
        FROM dbo.SysResources r
        INNER JOIN dbo.SysLogin l
          ON l.ActiveIDLogin2Resource = r.ActiveIDLogin2Resource
        INNER JOIN dbo.SysWorkRoomResource wr ON wr.IDResource = r.ResourceId
        INNER JOIN dbo.SysWorkRoom w ON w.IDWorkRoom = wr.IDWorkRoom
    """,
    "agent-scopes": """
        SELECT DISTINCT
               r.DisplayName, r.ResourceId, lr.IDLogin, l.FullName,
               w.IDWorkRoom, w.Code AS WorkRoomCode, w.Name AS WorkRoomName,
               wr.ResourceAccessType,
               c.ID AS IDCommunity, c.Name AS CommunityName,
               c.Description AS CommunityDescription, c.Code AS CommunityCode,
               e.organizationid, e.organization_no, e.accountname
        FROM dbo.SysResources r
        INNER JOIN dbo.SysResource2Agent ra
          ON ra.IDHumanResource = r.ResourceId AND ra.Active = 1
        INNER JOIN dbo.SysWorkRoomResource wr ON wr.IDResource = r.ResourceId
        INNER JOIN dbo.SysWorkRoom w ON w.IDWorkRoom = wr.IDWorkRoom
        INNER JOIN dbo.SysCommunity2WorkRoom cw ON cw.IDWorkRoom = w.IDWorkRoom
        INNER JOIN dbo.SysCommunity c ON c.ID = cw.IDCommunity
        INNER JOIN dbo.SysCommunity2Resource cr
          ON cr.IDCommunity = c.ID AND cr.IDResource = r.ResourceId
        INNER JOIN dbo.SysLogin2SysResource lr ON lr.IDResource = r.ResourceId
        INNER JOIN dbo.SysLogin l ON l.IDLogin = lr.IDLogin
        INNER JOIN dbo.SysCommunity2Company cc ON cc.IDCommunity = c.ID
        INNER JOIN dbo.Entity e ON e.ID = cc.IDCompany
    """,
}

DATASET_ORDER_BY = {
    "resources": "ResourceId",
    "logins": "IDLogin",
    "workrooms": "IDWorkRoom",
    "workroom-resources": "IDWorkRoom, ResourceId",
    "agent-scopes": "ResourceId, IDLogin, IDWorkRoom, IDCommunity",
}

# Stable, unique columns used for keyset pagination. Keeping this metadata
# separate from the SQL text avoids accepting column names from callers.
DATASET_CURSOR_COLUMNS = {
    "resources": ("ResourceId",),
    "logins": ("IDLogin",),
    "workrooms": ("IDWorkRoom",),
    "workroom-resources": ("IDWorkRoom", "ResourceId"),
    "agent-scopes": ("ResourceId", "IDLogin", "IDWorkRoom", "IDCommunity"),
}

ACTIVE_RESOURCE_AGENT = """
    SELECT TOP 1 IDAgentResource
    FROM dbo.SysResource2Agent WITH (NOLOCK)
    WHERE IDHumanResource = %s AND Active = 1
    ORDER BY CreatedUtc DESC
"""
