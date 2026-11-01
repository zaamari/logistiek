# Leverplanner

Berekent de snelste leverroute voor een leverdag met maximaal 6 kratten per rit.
Je start met een volle lading vanaf de startlocatie, laadt daarna steeds bij in de loods en eindigt in de loods.

## Starten

Dubbelklik `start.bat`. De planner opent in je browser op http://localhost:8765.

Eerste keer (of op een nieuwe pc): `pip install -r requirements.txt`

## Hoe het rekent

- **Rekenkern:** Google OR-Tools (geen AI; vaste regels, altijd dezelfde uitkomst).
- **Adressen:** PDOK Locatieserver (Kadaster). Het adres moet straat + huisnummer + plaats of postcode bevatten.
- **Reistijden:** OSRM (OpenStreetMap). Zonder internet wordt een schatting op basis van afstand gebruikt.
- **Twee berekeningen:** *zonder splitsen* (alleen bestellingen met Splitsen = ja worden verdeeld) en
  *met splitsen* (alles behalve Splitsen = nee). Het verschil laat zien bij welke klanten je moet vragen of splitsen mag.
- **Tijden:** lossen = 7 min voor de eerste krat + 3 min per volgende krat; laden in de loods = 30 min.
  De rit van start naar de eerste klant telt niet mee, zodat de planner bij voorkeur bij een verre klant begint.
- **Tijdvensters:** ingevulde vensters zijn harde afspraken. Voor klanten zonder venster stelt de planner een venster van 2 uur voor.

Alle waarden zijn aan te passen onder *Instellingen*.

## Excel

Download het sjabloon via de knop *Sjabloon*. Kolommen: Klant, Adres, Kratten, Splitsen (ja/nee/?), Venster van, Venster tot.
Losse kolommen *Postcode* en *Plaats* worden ook herkend.
