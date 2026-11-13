# kvd-stangda-auktioner
Arkiv av stängda auktioner från kvd.se med GitHub Actions

## Körning

Workflowet `scrape-kvd.yml` håller en kedja igång så att skrapningen inte beror på att GitHubs cron faktiskt startar. Varje jobb hämtar högst 50 auktioner, väntar ungefär en timme och upprepar det i cirka fem timmar. Därefter startar det nästa jobb själv. Cron `17 * * * *` är bara en reserv om kedjan har dött, och den startar inte en andra kedja om en redan kör. Takten är högst 50 detaljsidor per timme.

Kedjan startar inte av sig själv bara för att workflowet ändrats. Kör det en gång manuellt under Actions. För att stoppa det: stäng av workflowet.
