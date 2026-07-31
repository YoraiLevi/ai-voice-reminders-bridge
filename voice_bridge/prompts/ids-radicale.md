WHICH LISTS ARE MINE - the ACCOUNT disambiguates them, not the ids.
  Both lists live in the CalDAV account whose user name is "$radicale_user".
  "$inbox_list" (you dictate here)
  "$output_list" (answers arrive here)
  Lists with these names may also exist in OTHER accounts on this phone.
  Those are not mine - only the pair inside "$radicale_user" is.
  (This PC addresses them by server URL, which your phone never sees, so
  there is no id here for you to match. YOUR ids are local to your device -
  you are the only one who can read them, which is why the next section is
  something only you can do.)

PIN THEM ONCE - resolve by account the FIRST time, then never by name again.
  1. Find the two lists inside the "$radicale_user" account, and note
     whatever identifier your own tools give each one.
  2. From then on address them ONLY by those identifiers. A name lookup that
     picked the right list today can pick a different one tomorrow; an id
     cannot, and creating BY NAME can fail outright.
  3. If a write by a pinned id ever fails, re-derive it ONCE from the
     "$radicale_user" account - not from the name alone - and pin what
     you find. If that fails too, tell me: two failures is a changed setup,
     not something to retry.
