-- Ensure a fresh migration-only deployment can generate papers.
-- The larger generated seed remains available in seed/002_blueprints.sql.

insert into subjects (code, name, section, marks_min, marks_max, priority, sort_order) values
  ('GA',   'General Aptitude',                       'GA', 15, 15, 'Fixed',   0),
  ('PDS',  'Programming and Data Structures',        'CS', 10, 15, 'High',    1),
  ('MATH', 'Engineering Mathematics',                'CS', 13, 15, 'High',    2),
  ('ALGO', 'Algorithms',                             'CS',  7, 12, 'High',    3),
  ('OS',   'Operating Systems',                      'CS',  8, 12, 'High',    4),
  ('CN',   'Computer Networks',                      'CS',  8, 12, 'Medium',  5),
  ('COA',  'Computer Organization and Architecture', 'CS',  7, 10, 'Medium',  6),
  ('TOC',  'Theory of Computation',                  'CS',  7, 10, 'Medium',  7),
  ('DBMS', 'Databases',                              'CS',  6, 10, 'Medium',  8),
  ('CD',   'Compiler Design',                        'CS',  4,  6, 'Low',     9),
  ('DL',   'Digital Logic',                          'CS',  4,  6, 'Low',    10)
on conflict (code) do update set
  name = excluded.name,
  section = excluded.section,
  marks_min = excluded.marks_min,
  marks_max = excluded.marks_max,
  priority = excluded.priority,
  sort_order = excluded.sort_order;

insert into blueprints (name, duration_min, total_questions, total_marks)
select 'Standard-01', 180, 65, 100
where not exists (select 1 from blueprints where name = 'Standard-01');

insert into blueprint_rules (blueprint_id, subject_id, marks, qcount)
select b.id, s.id, v.marks, v.qcount
from blueprints b
join (values
  ('GA', 1, 5), ('GA', 2, 5),
  ('PDS', 1, 2), ('PDS', 2, 5),
  ('MATH', 1, 6), ('MATH', 2, 4),
  ('ALGO', 1, 1), ('ALGO', 2, 3),
  ('OS', 1, 2), ('OS', 2, 4),
  ('CN', 1, 3), ('CN', 2, 4),
  ('COA', 1, 1), ('COA', 2, 3),
  ('TOC', 1, 1), ('TOC', 2, 4),
  ('DBMS', 1, 5), ('DBMS', 2, 1),
  ('CD', 2, 2), ('DL', 1, 4)
) as v(code, marks, qcount) on true
join subjects s on s.code = v.code
where b.name = 'Standard-01'
on conflict (blueprint_id, subject_id, marks) do update
set qcount = excluded.qcount;
