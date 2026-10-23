-- seed/001_subjects.sql  |  your weightage table. Edit marks_min / marks_max any time; re-run make_blueprints.py afterwards.
insert into subjects (code, name, section, marks_min, marks_max, priority, sort_order) values
  ('GA',   'General Aptitude',                      'GA', 15, 15, 'Fixed',  0),
  ('PDS',  'Programming and Data Structures',       'CS', 10, 15, 'High',   1),
  ('MATH', 'Engineering Mathematics',               'CS', 13, 15, 'High',   2),
  ('ALGO', 'Algorithms',                            'CS',  7, 12, 'High',   3),
  ('OS',   'Operating Systems',                     'CS',  8, 12, 'High',   4),
  ('CN',   'Computer Networks',                     'CS',  8, 12, 'Medium', 5),
  ('COA',  'Computer Organization and Architecture','CS',  7, 10, 'Medium', 6),
  ('TOC',  'Theory of Computation',                 'CS',  7, 10, 'Medium', 7),
  ('DBMS', 'Databases',                             'CS',  6, 10, 'Medium', 8),
  ('CD',   'Compiler Design',                       'CS',  4,  6, 'Low',    9),
  ('DL',   'Digital Logic',                         'CS',  4,  6, 'Low',   10)
on conflict (code) do update set name = excluded.name, section = excluded.section, marks_min = excluded.marks_min,
  marks_max = excluded.marks_max, priority = excluded.priority, sort_order = excluded.sort_order;

-- topics = labels used when training the model (common.py CS_SUBJECTS) -> each belongs to one subject
insert into topics (subject_id, name)
select s.id, t.name from (values
  ('GA',   'General Aptitude'),
  ('PDS',  'Programming and Data Structures'),
  ('MATH', 'Discrete Mathematics'), ('MATH', 'Linear Algebra'), ('MATH', 'Calculus'), ('MATH', 'Probability and Statistics'),
  ('ALGO', 'Algorithms'),
  ('OS',   'Operating Systems'),
  ('CN',   'Computer Networks'),
  ('COA',  'Computer Organization and Architecture'),
  ('TOC',  'Theory of Computation'),
  ('DBMS', 'Databases'),
  ('CD',   'Compiler Design'),
  ('DL',   'Digital Logic')
) as t(code, name) join subjects s on s.code = t.code
on conflict (name) do nothing;
