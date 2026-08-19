CREATE UNIQUE INDEX IF NOT EXISTS "UQ_SysSolidSETInstance_Code_Normalized"
    ON public."SysSolidSETInstance" (LOWER(BTRIM("Code")));

CREATE UNIQUE INDEX IF NOT EXISTS "UQ_SysSolidSETInstance_BaseUrl_Normalized"
    ON public."SysSolidSETInstance" (LOWER(RTRIM(BTRIM("BaseUrl"), '/')));

