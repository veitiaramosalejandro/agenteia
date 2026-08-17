CREATE TABLE IF NOT EXISTS public."SysResourceIAKnowledge" (
    "ID" uuid NOT NULL DEFAULT gen_random_uuid(),
    "IDResource" uuid NOT NULL,
    "IDWorkRoom" uuid,
    "Title" varchar(255),
    "KnowledgeText" text NOT NULL,
    "Source" varchar(100) NOT NULL DEFAULT 'manual',
    "Stamp" timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    active boolean NOT NULL DEFAULT true,
    CONSTRAINT "PK_SysResourceIAKnowledge" PRIMARY KEY ("ID"),
    CONSTRAINT "FK_SysResourceIAKnowledge_SysResourceIA_IDResource"
        FOREIGN KEY ("IDResource")
        REFERENCES public."SysResourceIA" ("IDResource")
        ON UPDATE CASCADE
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS "IX_SysResourceIAKnowledge_IDResource_IDWorkRoom_active"
    ON public."SysResourceIAKnowledge" ("IDResource", "IDWorkRoom", active);

COMMENT ON TABLE public."SysResourceIAKnowledge" IS
    'Conocimiento privado o contextual perteneciente a cada agente IA.';
