# English and Spanish: review of the Spanish draft

Story: [English and Spanish](story-english-and-spanish-plan.md). Catalogue: `locale/es/LC_MESSAGES/django.po`.

The Spanish below is a draft by Claude. Please read each row and, where the Spanish is wrong or could be better, write your version in **Corrección**. Leave it empty if the draft is fine. Corrections are copied into the `.po` and the page is rebuilt with `python manage.py compilemessages`.

Conventions used in the draft (tell us if any should change):

- Doctors are addressed with *usted* ("su clínica", "Contáctenos", "puede apoyarse").
- Brand and proper nouns stay as they are: MyTreatPlan, VirtuaOrtho, TPS, KOL, MEA / MEA-T, team names, the email address, the registered address.
- "Dubai" is "Dubái" and "Dublin" is "Dublín" in Spanish.
- Spanish uses sentence case for headings ("Alta calidad", not "Alta Calidad"), except "Ortodoncia Digital" as a service name.
- "&amp;" in the English becomes "y" in Spanish.
- Rows in `code` contain markup (`<strong>`, `<br>` …) or placeholders (`%(n)s`, `{n}`). Keep those exactly; the tests fail if a tag or placeholder is lost.
- Job titles use the feminine form for Belén, Adina, Iliyana and Marta.

## Approval

| | |
|---|---|
| Status | Pending team review |
| Reviewed by | |
| Date | |
| Corrections applied to `.po` | |


## Header, navigation and footer (base.html)

| # | English | Español (borrador) | Corrección |
|---|---|---|---|
| 1 | Digital Orthodontics Made Simple | Ortodoncia Digital sin complicaciones | |
| 2 | Treatment planning services, remote virtual orthodontist support and education for orthodontic practices. Offices in Dublin, Dubai and Madrid. | Servicios de planificación de tratamientos, soporte de ortodoncista virtual a distancia y formación para clínicas de ortodoncia. Oficinas en Dublín, Dubái y Madrid. | |
| 3 | Menu | Menú | |
| 4 | Main | Principal | |
| 5 | home | inicio | |
| 6 | what we do | qué hacemos | |
| 7 | about us | sobre nosotros | |
| 8 | who we are | quiénes somos | |
| 9 | where we are | dónde estamos | |
| 10 | contact | contacto | |
| 11 | Language | Idioma | |
| 12 | MyTPDSO L.L.C.-FZ | MyTPDSO L.L.C.-FZ | |
| 13 | License number | Número de licencia | |
| 14 | Registered address at Meydan Grandstand, 6th Floor, Meydan Road, Nad Al Sheba, Dubai, U.A.E. | Domicilio social en Meydan Grandstand, 6th Floor, Meydan Road, Nad Al Sheba, Dubái, EAU. | |
| 15 | `Copyright &copy; %(year)s <strong>MytreatPlan</strong>. All Rights Reserved` | `Copyright &copy; %(year)s <strong>MytreatPlan</strong>. Todos los derechos reservados` | |

## Hero (home.html)

| # | English | Español (borrador) | Corrección |
|---|---|---|---|
| 16 | Digital Orthodontics | Ortodoncia Digital | |
| 17 | Made Simple | Sin complicaciones | |

## What we do

| # | English | Español (borrador) | Corrección |
|---|---|---|---|
| 18 | What we do | Qué hacemos | |
| 19 | High Quality | Alta calidad | |
| 20 | Premium Digital Orthodontics Services | Servicios premium de Ortodoncia Digital | |
| 21 | Tailored TPS | TPS a medida | |
| 22 | Digital treatment plan in 3D showing bracket placement and tooth movement | Plan de tratamiento digital en 3D que muestra la colocación de los brackets y el movimiento dental | |
| 23 | `with <strong>Mentoring and Clinical Support</strong>` | `con <strong>mentoría y soporte clínico</strong>` | |
| 24 | VirtuaOrtho | VirtuaOrtho | |
| 25 | Orthodontist reviewing a clinical case remotely with digital planning tools | Ortodoncista revisando un caso clínico a distancia con herramientas de planificación digital | |
| 26 | Full remote VIRTUAL ORTHODONTIST support (case study review, diagnostic confirmation, treatment design assistance, digital planning, full online clinical support) | Soporte completo de ORTODONCISTA VIRTUAL a distancia (revisión de casos, confirmación del diagnóstico, asistencia en el diseño del tratamiento, planificación digital y soporte clínico online completo) | |
| 27 | Education | Formación | |
| 28 | Orthodontics masterclass with dental professionals | Masterclass de ortodoncia con profesionales dentales | |
| 29 | One to one and workshops with a highly practical approach | Sesiones individuales y talleres con un enfoque muy práctico | |

## About us

| # | English | Español (borrador) | Corrección |
|---|---|---|---|
| 30 | About us | Sobre nosotros | |
| 31 | `<strong>MyTreatPlan</strong> is the evolution of one of the most successful TPS companies in Europe. Starting in 2017, the founder team continued innovating through the years while developing the best and highest quality <strong><em>treatment planning services in the market.</em></strong>` | `<strong>MyTreatPlan</strong> es la evolución de una de las empresas de TPS con más éxito de Europa. Desde 2017, el equipo fundador no ha dejado de innovar, desarrollando con la máxima calidad los mejores <strong><em>servicios de planificación de tratamientos del mercado.</em></strong>` | |
| 32 | `Now in 2026, with all the immense experience gathered, we present a new brand and a whole new set of products and services for <strong>Digital Orthodontics</strong> intended to push dental practices to a higher level in terms of efficiency and profitability.` | `Ahora, en 2026, con toda la experiencia acumulada, presentamos una nueva marca y una gama completamente nueva de productos y servicios de <strong>Ortodoncia Digital</strong>, pensados para llevar a las clínicas dentales a un nivel superior de eficiencia y rentabilidad.` | |
| 33 | Our unique approach, focused on being partners of our Doctors and Practices and accompanying them during their treatments makes us much more than just a service provider but a trustworthy Orthodontic Center in which you can always rely for helping you taking care of your patients. | Nuestro enfoque, centrado en ser socios de nuestros Doctores y Clínicas y en acompañarles durante sus tratamientos, nos convierte en mucho más que un proveedor de servicios: somos un Centro de Ortodoncia de confianza en el que siempre puede apoyarse para cuidar de sus pacientes. | |
| 34 | +50.000 | +50.000 | |
| 35 | Plannings successfully delivered by our team | Planificaciones entregadas con éxito por nuestro equipo | |
| 36 | +700 | +700 | |
| 37 | Satisfied Customers | Clientes satisfechos | |

## Who we are (team carousel)

| # | English | Español (borrador) | Corrección |
|---|---|---|---|
| 38 | `Who<br>we are` | `Quiénes<br>somos` | |
| 39 | carousel | carrusel | |
| 40 | Our team | Nuestro equipo | |
| 41 | slide | diapositiva | |
| 42 | `%(n)s of %(total)s: %(name)s` | `%(n)s de %(total)s: %(name)s` | |
| 43 | `Co-Founder &amp; Clinical<br>Chief Officer` | `Cofundadora y Directora<br>Clínica` | |
| 44 | With over 19 years of experience as Orthodontist, having worked for the aligners industry, professor at various Universities and KOL for a well renown brand, Belén is our Clinical Cornerstone. | Con más de 19 años de experiencia como ortodoncista, tras haber trabajado para la industria de los alineadores, como profesora en varias universidades y como KOL de una marca de renombre, Belén es nuestro pilar clínico. | |
| 45 | `Co-Founder, Chair &amp; CEO` | Cofundador, Presidente y CEO | |
| 46 | As a telecomm engineer with over 20 years of wide experience, Antonio is in charge of making our business run, both technically and at managing levels. | Ingeniero de telecomunicaciones con más de 20 años de amplia experiencia, Antonio se encarga de que nuestro negocio funcione, tanto en lo técnico como en la gestión. | |
| 47 | MEA-T Regional Manager | Directora Regional MEA-T | |
| 48 | With over 14 years of experience in the Aligners Market all over the MEA-T region, Adina leads our expansion in the Middle East, Africa and Turkey region. | Con más de 14 años de experiencia en el mercado de alineadores en toda la región MEA-T, Adina lidera nuestra expansión en Oriente Medio, África y Turquía. | |
| 49 | Commercial Director | Director Comercial | |
| 50 | With an Executive MBA and over 16 years of extensive experience in the Dental market, Albert is the person who leads our Marketing and Sales department and an invaluable asset for our company. | Con un Executive MBA y más de 16 años de amplia experiencia en el sector dental, Albert dirige nuestro departamento de Marketing y Ventas y es un activo de valor incalculable para nuestra empresa. | |
| 51 | `Senior CAD Designer<br>&amp; Team Leader` | `Diseñadora CAD sénior<br>y jefa de equipo` | |
| 52 | Iliyana has both Dental Prosthetist and Hygienist education background. She has been working as a digital planner for orthodontics for more than 7 years now. | Iliyana tiene formación como protésica dental e higienista. Lleva más de 7 años trabajando como planificadora digital de ortodoncia. | |
| 53 | `Senior CAD Designer<br>&amp; DM Coordinator` | `Diseñadora CAD sénior<br>y coordinadora DM` | |
| 54 | Marta has both Dental Prosthetist and Hygienist background. She has been working as a digital planner for orthodontics for more than 8 years now. | Marta tiene formación como protésica dental e higienista. Lleva más de 8 años trabajando como planificadora digital de ortodoncia. | |
| 55 | Previous | Anterior | |
| 56 | Next | Siguiente | |
| 57 | Team members | Miembros del equipo | |
| 58 | `Go to position {n} of {total}` | `Ir a la posición {n} de {total}` | |

## Where we are

| # | English | Español (borrador) | Corrección |
|---|---|---|---|
| 59 | Where we are | Dónde estamos | |
| 60 | Dublin | Dublín | |
| 61 | `Europe &amp; North America` | Europa y Norteamérica | |
| 62 | `<strong>European HQ.</strong><br>Dublin &amp; Cork offices attending UK, Ireland &amp; North Europe.` | `<strong>Sede europea.</strong><br>Oficinas en Dublín y Cork que atienden Reino Unido, Irlanda y el norte de Europa.` | |
| 63 | Dubai | Dubái | |
| 64 | `Global Headquarters<br>&amp; MEA Market` | `Sede central global<br>y mercado MEA` | |
| 65 | `<strong>Global HQ.</strong><br>Attending MEA &amp; Turkey Markets.` | `<strong>Sede central global.</strong><br>Atiende los mercados de MEA y Turquía.` | |
| 66 | Madrid | Madrid | |
| 67 | `Iberia &amp; Rest of America` | Iberia y resto de América | |
| 68 | `Offices attending Iberia &amp; Rest of America.` | Oficinas que atienden Iberia y el resto de América. | |

## Contact

| # | English | Español (borrador) | Corrección |
|---|---|---|---|
| 69 | Contact | Contacto | |
| 70 | Get in touch | Contáctenos | |
