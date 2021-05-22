from __future__ import annotations
from logging import warn
from typing import *
from fhirclient.models.coding import Coding
from fhirclient.models.quantity import Quantity
if TYPE_CHECKING:
    from etl_process import ResourceName

import pandas as pd
import numpy as np
import re
from datetime import datetime
from time import gmtime, strftime

from fhirclient import client
from fhirclient import server

# Fhir Object Imports
from fhirclient.models.encounter import Encounter
from fhirclient.models.patient import Patient
from fhirclient.models.condition import Condition

# Attribute Imports
from fhirclient.models.period import Period
from fhirclient.models.fhirdate import FHIRDate
from fhirclient.models.identifier import Identifier
from fhirclient.models.humanname import HumanName
from fhirclient.models.address import Address
from fhirclient.models.fhirreference import FHIRReference
from fhirclient.models.reference import Reference
from fhirclient.models.codeableconcept import CodeableConcept
from fhirclient.models.observation import Observation
from fhirclient.models.procedure import Procedure


class ObjectCreator:

    _observation_loinc_mapping_dict: Dict[str, Tuple[str, str, str]] = {
        'Apnoe Index (n/h)':        ('90562-0', '{events}/h',   'Apnea index'),
        'Hypnopnoe Index (n/h)':    ('90561-2', '{events}/h',   'Hypopnea index'),
        'RERA Index (n/h)':         ('90565-3', '{events}/h',   'Respiratory effort-related arousal index'),
        'AHI':                      ('69990-0', '{events}/h',   'Apnea hypopnea index 24 hour'),
        'RDI':                      ('90566-1', '{events}/h',   'Respiratory disturbance index'),
        'RDI / AHI (n/h)':          ('',        '{events}/h',   ''),
        'Schlaflatenz (min)':       ('',        'min',          ''),
        'Alter (Jahre)':            ('30525-0', 'a',            'Age'),
        'Arousal Index (n/h)':      ('',        '{events}/h',   ''),
        'Schnarchzeit (min)':       ('',        'min',          ''),
        'totale Schlafzeit (min)':  ('93832-4', 'min',          'Sleep duration'),
        'Schnarchen Total (%TST)':  ('',        '%',            ''),
        'PLM Index':                ('',        '',             ''),
    }

    # ------------------------------ Helper Methods ------------------------------ #

    @staticmethod
    def _parse_date_time(datetime_str: str) -> FHIRDate:

        # Splitting into yyyy-mm-dd and hh:mm:ss.zzz
        # Adding T between
        case_date = str(datetime_str).split(' ', maxsplit=-1)[0]                                 # yyyy-mm-dd
        case_time = str(datetime_str).split(' ', maxsplit=-1)[1].split('.', maxsplit=-1)[0]      # hh:mm:ss
        case_ms = str(datetime_str).split(' ', maxsplit=-1)[1].split('.', maxsplit=-1)[1][:3]    # miliseconds
        case_tz = strftime('%z', gmtime())[:3] + ':' + strftime('%z', gmtime())[1:-2]       # timezone...germany = 00:00
        return FHIRDate(case_date + 'T' + case_time + '.' + case_ms + case_tz)

    @staticmethod
    def _parse_date(date_str: str) -> FHIRDate:
           
        DOT = r'\.'
        DATE_PATTERN = re.compile(
            r'(?P<day>\d{2})' + DOT +   \
            r'(?P<month>\d{2})' + DOT + \
            r'(?P<year>\d{4})'
        )
        match = DATE_PATTERN.match(date_str)
        assert match is not None

        day = int(match.group('day'))
        month = int(match.group('month'))
        year = int(match.group('year'))

        ts = pd.Timestamp(day=day, month=month, year=year) #type: ignore
        return FHIRDate(ts.strftime('%Y-%m-%d'))

    @staticmethod
    def _map_gender(gender: str) -> str:
        if gender == "m":
            return "male"
        elif gender == "f" or gender == "w":
            return "female"
        else:
            raise Exception(f'Unknown gender code {gender}')

    @staticmethod
    def _construct_reference(resource_name: ResourceName, ref_id: str) -> Reference:
        resource_str = resource_name.value

        ref = FHIRReference()
        ref.type = resource_str
        ref.reference = f'{resource_str}/{ref_id}'

        return ref

    @staticmethod
    def _construct_identifier(identifier: str) -> List[Identifier]:
        ident = Identifier()
        ident.value = identifier

        return [ident]

    @staticmethod
    def _resolve_observation_column(column_name: str) -> Tuple[str, str]:

        if column_name not in ObjectCreator._observation_loinc_mapping_dict.keys():
            raise Exception(f'Invalid column name: "{column_name}"')

        code, unit = ObjectCreator._observation_loinc_mapping_dict[column_name]

        if code == '':
            warn(f'Code is unknown for column name: "{column_name}"')

        return ObjectCreator._observation_loinc_mapping_dict[column_name]

    # --------------------------- Object create methods -------------------------- #

    @staticmethod
    def create_encounter(case_row: pd.Series, subject_ref_id: str) -> Encounter:

        period = Period()
        period.start = ObjectCreator._parse_date_time(case_row.admission)
        period.end = ObjectCreator._parse_date_time(case_row.discharge)

        encounter = Encounter()
        encounter.identifier = ObjectCreator._construct_identifier(case_row.id)
        encounter.resource_type = 'Encounter'
        encounter.status = 'finished'
        encounter.period = period
        encounter.subject = ObjectCreator._construct_reference(ResourceName.PATIENT, subject_ref_id)

        return encounter


    @staticmethod
    def create_patient(patient_row: pd.Series) -> Patient:

        name = HumanName()
        name.given = [patient_row.first_name]
        name.family = patient_row.last_name
        
        address = Address()
        address.line = [patient_row.street]
        address.postalCode = str(patient_row.zip)
        address.city = patient_row.city

        patient = Patient()
        patient.identifier = ObjectCreator._construct_identifier(patient_row.id)
        patient.name = [name]
        patient.gender = ObjectCreator._map_gender(patient_row.sex)
        patient.birthDate = ObjectCreator._parse_date(patient_row.date_of_birth)
        patient.address = [address]

        return patient


    @staticmethod
    def create_condition(diagnose_row: pd.Series, subject_ref_id, encounter_ref_id) -> Condition:

        assert diagnose_row.sytem == 'ICD-10-GM'
        assert diagnose_row.version == '2020'

        coding = Coding()
        coding.system = 'http://fhir.de/CodeSystem/dimdi/icd-10-gm'
        coding.version = '2020'
        coding.code = diagnose_row.code
        coding.display: diagnose_row.type

        code = CodeableConcept()
        code.coding = [coding]

        condition = Condition()
        condition.identifier = ObjectCreator._construct_identifier(diagnose_row.id)
        condition.subject = ObjectCreator._construct_reference(ResourceName.PATIENT, subject_ref_id)
        # TODO: Encounter is missing here!
        condition.code = code
        condition.category = 'encounter-diagnosis'

        return condition

    @staticmethod
    def create_observation(observation_row: pd.Series, subject_ref_id: str) -> List[Observation]:
        observation_list: List[Observation] = []

        for column_name, (obs_loinc_code, unit, display) in ObjectCreator._observation_loinc_mapping_dict:

            if obs_loinc_code == '':
                warn(f'Code is unknown for column name: "{column_name}"')

            coding = Coding()
            coding.system = 'http://loinc.org'
            coding.version = '2.69'
            coding.code = obs_loinc_code
            coding.display = display

            code = CodeableConcept()
            code.coding = [coding]

            quantity = Quantity()
            quantity.value = observation_row[column_name]
            quantity.unit = unit
            code = coding

            observation = Observation()
            observation.status = 'final'
            observation.code = code
            observation.subject = ObjectCreator._construct_reference(ResourceName.PATIENT, subject_ref_id)
            # TODO: Encounter is missing here!
            observation.valueQuantity = quantity

            observation_list.append(observation)

        return observation_list

    @staticmethod
    def create_procedure(procedure_row: pd.Series, subject_ref_id) -> Procedure:
        assert procedure_row.code_system == 'OPS'
        assert procedure_row.code_version == '2020'

        coding = Coding()
        coding.system = 'http://fhir.de/CodeSystem/dimdi/ops' 
        coding.version = '2020'
        coding.code = procedure_row.code

        code = CodeableConcept()
        code.coding = [coding]

        procedure = Procedure()
        procedure.identifier = ObjectCreator._construct_identifier(procedure_row.id)
        procedure.status = 'completed'
        procedure.subject = ObjectCreator._construct_reference(ResourceName.PATIENT, subject_ref_id)
        # TODO: Encounter is missing here!
        procedure.code = code

        return procedure